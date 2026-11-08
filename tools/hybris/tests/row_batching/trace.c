#define main descriptor_reuse_main
#define resolve descriptor_resolve
#include "../descriptor_reuse/trace.c"
#undef resolve
#undef main

static unsigned phase, next_pixel;
static struct hybris_bc_push batch;
static VkDeviceSize batch_source;
static VkBufferImageCopy expected_region;
static uint64_t checked_pixels;

static VKAPI_ATTR void VKAPI_CALL audit_barrier(VkCommandBuffer c,
    VkPipelineStageFlags before, VkPipelineStageFlags after, VkDependencyFlags flags,
    uint32_t memory_count, const VkMemoryBarrier *memory, uint32_t buffer_count,
    const VkBufferMemoryBarrier *buffer, uint32_t image_count, const VkImageMemoryBarrier *image)
{
    pipeline_barrier(c, before, after, flags, memory_count, memory, buffer_count, buffer, image_count, image);
    if (buffer->buffer == active_image->blocks) {
        if (before == VK_PIPELINE_STAGE_TRANSFER_BIT) {
            assert(phase == 0 && after == VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
            assert(buffer->srcAccessMask == VK_ACCESS_TRANSFER_WRITE_BIT);
            assert(buffer->dstAccessMask == VK_ACCESS_SHADER_READ_BIT);
            phase = 1;
        } else {
            assert(phase == 5 && before == VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
            assert(after == VK_PIPELINE_STAGE_TRANSFER_BIT);
            assert(buffer->srcAccessMask == VK_ACCESS_SHADER_READ_BIT);
            assert(buffer->dstAccessMask == (VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT));
            phase = 6;
        }
    } else {
        assert(buffer->buffer == HANDLE(VkBuffer, 50));
        if (before == VK_PIPELINE_STAGE_TRANSFER_BIT) {
            assert((phase == 1 || phase == 5) && after == VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
            assert(buffer->srcAccessMask == VK_ACCESS_TRANSFER_READ_BIT);
            assert(buffer->dstAccessMask == VK_ACCESS_SHADER_WRITE_BIT);
            phase = 2;
        } else {
            assert(phase == 3 && next_pixel == batch.pixels);
            assert(before == VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT && after == VK_PIPELINE_STAGE_TRANSFER_BIT);
            assert(buffer->srcAccessMask == VK_ACCESS_SHADER_WRITE_BIT);
            assert(buffer->dstAccessMask == VK_ACCESS_TRANSFER_READ_BIT);
            phase = 4;
        }
    }
}

static VKAPI_ATTR void VKAPI_CALL audit_dispatch(VkCommandBuffer c, uint32_t x, uint32_t y, uint32_t z)
{
    dispatch(c, x, y, z);
    assert(phase == 2 || phase == 3);
    VkDeviceSize source = sets[VALUE(bound_set)].buffer[0].offset + (uint64_t)pushed.source_word * 4;
    if (phase == 2) {
        batch = pushed;
        batch_source = source;
        next_pixel = 0;
    } else {
        assert(source == batch_source && pushed.mode == batch.mode);
        assert(pushed.width == batch.width && pushed.height == batch.height);
        assert(pushed.pixels == batch.pixels && pushed.row_blocks == batch.row_blocks);
        assert(pushed.layer_blocks == batch.layer_blocks && pushed.destination_word == batch.destination_word);
    }
    assert(pushed.first_pixel == next_pixel && !(pushed.first_pixel & 63));
    next_pixel += pushed.chunk_pixels;
    phase = 3;
}

static VKAPI_ATTR void VKAPI_CALL audit_copy(VkCommandBuffer c, VkBuffer buffer,
    VkImage image, VkImageLayout layout, uint32_t count, const VkBufferImageCopy *copy)
{
    copy_to_image(c, buffer, image, layout, count, copy);
    assert(phase == 4 && next_pixel == batch.pixels && batch.destination_word == 0);
    assert(copy->imageExtent.width == batch.width && copy->imageExtent.height == batch.height);
    const VkImageSubresourceLayers *sub = &copy->imageSubresource;
    const VkImageSubresourceLayers *expected = &expected_region.imageSubresource;
    assert(sub->mipLevel == expected->mipLevel);
    assert(sub->baseArrayLayer >= expected->baseArrayLayer &&
        sub->baseArrayLayer < expected->baseArrayLayer + expected->layerCount);
    assert(copy->imageOffset.x >= expected_region.imageOffset.x &&
        copy->imageOffset.y >= expected_region.imageOffset.y);
    assert((uint64_t)copy->imageOffset.x + batch.width <=
        (uint64_t)expected_region.imageOffset.x + expected_region.imageExtent.width);
    assert((uint64_t)copy->imageOffset.y + batch.height <=
        (uint64_t)expected_region.imageOffset.y + expected_region.imageExtent.height);
    VkExtent3D extent = hybris_bc_image_extent(active_image, sub->mipLevel);
    uint64_t row_bytes = ((uint64_t)extent.width + 3) / 4 * active_image->block_bytes;
    uint64_t layer_base = active_image->mip_offset[sub->mipLevel] +
        sub->baseArrayLayer * hybris_bc_image_layer_size(active_image, sub->mipLevel);
    for (uint32_t y = 0; y < batch.height; ++y) {
        uint32_t iy = (uint32_t)copy->imageOffset.y + y;
        uint64_t row_source = batch_source + (uint64_t)(y / 4) * batch.row_blocks * active_image->block_bytes;
        for (uint32_t x = 0; x < batch.width; ++x) {
            uint32_t ix = (uint32_t)copy->imageOffset.x + x;
            uint64_t actual = row_source + (uint64_t)(x / 4) * active_image->block_bytes;
            uint64_t reference = layer_base + (uint64_t)(iy / 4) * row_bytes +
                (uint64_t)(ix / 4) * active_image->block_bytes;
            assert(actual == reference && (y & 3) * 4 + (x & 3) == (iy & 3) * 4 + (ix & 3));
            ++checked_pixels;
        }
        printf("R image=%" PRIu64 " mip=%u layer=%u y=%u x=%d width=%u source=%" PRIu64 " within_y=%u mode=%u\n",
            VALUE(image), sub->mipLevel, sub->baseArrayLayer, iy, copy->imageOffset.x,
            batch.width, row_source, y & 3, batch.mode);
    }
    phase = 5;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL resolve(VkDevice d, const char *name)
{
    if (!strcmp(name, "vkCmdPipelineBarrier")) return (PFN_vkVoidFunction)audit_barrier;
    if (!strcmp(name, "vkCmdDispatch")) return (PFN_vkVoidFunction)audit_dispatch;
    if (!strcmp(name, "vkCmdCopyBufferToImage")) return (PFN_vkVoidFunction)audit_copy;
    return descriptor_resolve(d, name);
}

struct batch_case {
    struct test_case region;
    uint32_t dispatch_limit;
};

int main(void)
{
    const struct batch_case cases[] = {
        {{"full-4096", VK_FORMAT_BC7_UNORM_BLOCK,4096,4096,1,1,0,0,1,0,0,4096,4096,134217728,0,0},65535},
        {{"full-2048", VK_FORMAT_BC7_UNORM_BLOCK,2048,2048,1,1,0,0,1,0,0,2048,2048,134217728,0,0},65535},
        {{"full-1024", VK_FORMAT_BC7_UNORM_BLOCK,1024,1024,1,1,0,0,1,0,0,1024,1024,134217728,0,0},65535},
        {{"full-128", VK_FORMAT_BC7_UNORM_BLOCK,128,128,1,1,0,0,1,0,0,128,128,134217728,0,0},65535},
        {{"scratch-exact", VK_FORMAT_BC7_UNORM_BLOCK,1024,16,1,1,0,0,1,0,0,1024,16,134217728,0,0},65535},
        {{"scratch-tail", VK_FORMAT_BC7_UNORM_BLOCK,1024,17,1,1,0,0,1,0,0,1024,17,134217728,0,0},65535},
        {{"odd-capacity", VK_FORMAT_BC7_UNORM_BLOCK,1025,35,1,1,0,0,1,0,0,1025,35,134217728,0,0},65535},
        {{"full-width-y-stripe", VK_FORMAT_BC7_UNORM_BLOCK,1024,33,1,1,0,0,1,0,4,1024,29,134217728,0,0},65535},
        {{"mip-array", VK_FORMAT_BC7_UNORM_BLOCK,4096,2048,4,3,2,1,2,0,0,1024,512,134217728,0,0},65535},
        {{"rgb8-odd", VK_FORMAT_BC1_RGB_UNORM_BLOCK,9,7,1,3,0,0,3,0,0,9,7,134217728,1,0},65535},
        {{"bc4-odd", VK_FORMAT_BC4_SNORM_BLOCK,9,129,1,3,0,0,3,0,0,9,129,134217728,0,0},65535},
        {{"bc6-rgb16-odd", VK_FORMAT_BC6H_UFLOAT_BLOCK,65,129,1,2,0,0,2,0,0,65,129,134217728,4,0},65535},
        {{"bc6-rgba16", VK_FORMAT_BC6H_SFLOAT_BLOCK,1024,17,1,1,0,0,1,0,0,1024,17,134217728,0,0},65535},
        {{"small-scratch", VK_FORMAT_BC7_UNORM_BLOCK,4,4,1,1,0,0,1,0,0,4,4,64,0,0},65535},
        {{"limit-exact-batch", VK_FORMAT_BC7_UNORM_BLOCK,128,128,1,1,0,0,1,0,0,128,128,16384,0,0},65535},
        {{"limit-over-fallback", VK_FORMAT_BC7_UNORM_BLOCK,128,128,1,1,0,0,1,0,0,128,128,16368,0,0},65535},
        {{"x-subrect-fallback", VK_FORMAT_BC7_UNORM_BLOCK,1024,1024,1,1,0,0,1,4,4,1020,1020,134217728,0,0},65535},
        {{"width-subrect-fallback", VK_FORMAT_BC7_UNORM_BLOCK,1024,1024,1,1,0,0,1,0,4,512,1020,134217728,0,0},65535},
        {{"width-over-fallback", VK_FORMAT_BC7_UNORM_BLOCK,4097,9,1,1,0,0,1,0,0,4097,9,134217728,0,0},65535},
        {{"source-over-4gib", VK_FORMAT_BC7_UNORM_BLOCK,16384,16384,4,20,3,18,2,1968,2036,80,12,134217728,0,0},65535},
        {{"chunked-dispatch", VK_FORMAT_BC7_UNORM_BLOCK,128,128,1,1,0,0,1,0,0,128,128,134217728,0,0},1},
        {{"consecutive-regions", VK_FORMAT_BC7_UNORM_BLOCK,1024,1024,1,1,0,0,1,0,0,1024,1024,134217728,0,1},65535},
    };
    setbuf(stdout, NULL);
    for (unsigned n = 0; n < sizeof(cases) / sizeof(cases[0]); ++n) {
        const struct test_case *test = &cases[n].region;
        memset(sets, 0, sizeof(sets));
        allocations = updates = pools = dispatches = copies = barriers = 0;
        checked_pixels = 0;
        limits = (VkPhysicalDeviceLimits){.maxStorageBufferRange=test->limit,
            .minStorageBufferOffsetAlignment=256,
            .maxComputeWorkGroupCount={cases[n].dispatch_limit,65535,65535},
            .maxComputeWorkGroupSize={1024,1024,64},.maxComputeWorkGroupInvocations=1024};
        struct hybris_bc_decoder decoder = {.device=HANDLE(VkDevice,1),.resolver=resolve,
            .descriptor_layout=HANDLE(VkDescriptorSetLayout,2),.layout=HANDLE(VkPipelineLayout,3),
            .pipeline=HANDLE(VkPipeline,4)};
        VkPhysicalDeviceMemoryProperties memory = {0};
        struct hybris_bc_transfer transfer;
        hybris_bc_transfer_init(&transfer,&decoder,&memory,&limits);
        transfer.scratch = HANDLE(VkBuffer,50);
        scratch_size = transfer.scratch_size = test->limit < 65536 ? test->limit : 65536;
        struct hybris_bc_command_state state = {0};
        for (unsigned invocation = 0; invocation <= test->consecutive; ++invocation) {
            VkImageCreateInfo info = {.sType=VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                .imageType=VK_IMAGE_TYPE_2D,.format=test->format,
                .extent={test->width,test->height,1},.mipLevels=test->mips,.arrayLayers=test->layers,
                .samples=VK_SAMPLE_COUNT_1_BIT,.tiling=VK_IMAGE_TILING_OPTIMAL,
                .usage=VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT};
            struct hybris_bc_image image;
            assert(hybris_bc_image_describe(&info,&image) == VK_SUCCESS);
            image.blocks=HANDLE(VkBuffer,100+invocation);
            image.image=HANDLE(VkImage,200+invocation);
            image.decoded_format=hybris_bc_image_format(info.format,test->rgb_mask);
            active_image=&image;
            expected_region=(VkBufferImageCopy){
                .imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,test->mip,test->layer,test->layer_count},
                .imageOffset={(int32_t)test->x,(int32_t)test->y,0},.imageExtent={test->w,test->h,1}};
            phase=0;
            printf("CASE name=%s invocation=%u image=%" PRIu64 " mip=%u layer=%u layers=%u x=%u y=%u width=%u height=%u scratch=%" PRIu64 "\n",
                test->name,invocation,VALUE(image.image),test->mip,test->layer,test->layer_count,
                test->x,test->y,test->w,test->h,scratch_size);
            assert(hybris_bc_decode_image(&transfer,HANDLE(VkCommandBuffer,10),&state,
                &image,VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,&expected_region) == VK_SUCCESS);
            assert(phase == 6 && state.error == VK_SUCCESS);
        }
        printf("COUNTS name=%s sets=%u updates=%u pools=%u dispatches=%u copies=%u barriers=%u pixels=%" PRIu64 " scratch=%" PRIu64 "\n",
            test->name,allocations,updates,pools,dispatches,copies,barriers,checked_pixels,scratch_size);
        hybris_bc_transfer_finish(&transfer);
    }
    return 0;
}
