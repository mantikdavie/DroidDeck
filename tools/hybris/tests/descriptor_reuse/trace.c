#define VK_NO_PROTOTYPES
#include "bc_transfer.h"
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HANDLE(type, n) ((type)(uintptr_t)(n))
#define VALUE(h) ((uint64_t)(uintptr_t)(h))
#define MAX_SETS 65536

struct descriptor {
    VkDescriptorBufferInfo buffer[2];
    unsigned writes;
};
static struct descriptor sets[MAX_SETS];
static unsigned allocations, updates, pools, dispatches, copies, barriers;
static VkPhysicalDeviceLimits limits;
static const struct hybris_bc_image *active_image;
static VkDescriptorSet bound_set;
static struct hybris_bc_push pushed;
static VkDeviceSize scratch_size;

static VKAPI_ATTR VkResult VKAPI_CALL create_pool(VkDevice d,
    const VkDescriptorPoolCreateInfo *info, const VkAllocationCallbacks *a,
    VkDescriptorPool *out) {
    (void)d; (void)a;
    assert(info->maxSets == 64 && info->poolSizeCount == 1);
    assert(info->pPoolSizes[0].descriptorCount == 128);
    *out = HANDLE(VkDescriptorPool, ++pools);
    return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL allocate_sets(VkDevice d,
    const VkDescriptorSetAllocateInfo *info, VkDescriptorSet *out) {
    (void)d;
    assert(info->descriptorSetCount == 1 && allocations + 1 < MAX_SETS);
    *out = HANDLE(VkDescriptorSet, ++allocations);
    return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL update_sets(VkDevice d, uint32_t count,
    const VkWriteDescriptorSet *writes, uint32_t copy_count, const VkCopyDescriptorSet *copy) {
    (void)d; (void)copy;
    assert(count == 2 && copy_count == 0);
    assert(writes[0].dstSet == writes[1].dstSet);
    struct descriptor *set = &sets[VALUE(writes[0].dstSet)];
    assert(set->writes == 0);
    for (unsigned i = 0; i < 2; ++i) {
        assert(writes[i].dstBinding == i && writes[i].descriptorCount == 1);
        assert(writes[i].descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER);
        set->buffer[i] = *writes[i].pBufferInfo;
        const VkDescriptorBufferInfo *b = &set->buffer[i];
        assert(b->range && b->range <= limits.maxStorageBufferRange);
        assert(b->offset % limits.minStorageBufferOffsetAlignment == 0);
        VkDeviceSize size = i ? scratch_size : active_image->block_size;
        assert(b->offset <= size && b->range <= size - b->offset);
        assert(b->buffer == (i ? HANDLE(VkBuffer, 50) : active_image->blocks));
    }
    set->writes = 1;
    ++updates;
}

static VKAPI_ATTR void VKAPI_CALL pipeline_barrier(VkCommandBuffer c,
    VkPipelineStageFlags before, VkPipelineStageFlags after, VkDependencyFlags flags,
    uint32_t memory_count, const VkMemoryBarrier *memory, uint32_t buffer_count,
    const VkBufferMemoryBarrier *buffer, uint32_t image_count, const VkImageMemoryBarrier *image) {
    (void)c; (void)memory; (void)image;
    assert(flags == 0 && memory_count == 0 && buffer_count == 1 && image_count == 0);
    assert(buffer->offset == 0 && buffer->size == VK_WHOLE_SIZE);
    assert(buffer->srcQueueFamilyIndex == VK_QUEUE_FAMILY_IGNORED);
    assert(buffer->dstQueueFamilyIndex == VK_QUEUE_FAMILY_IGNORED);
    printf("B buffer=%" PRIu64 " stage=%u,%u access=%u,%u\n", VALUE(buffer->buffer),
        before, after, buffer->srcAccessMask, buffer->dstAccessMask);
    ++barriers;
}

static VKAPI_ATTR void VKAPI_CALL bind_pipeline(VkCommandBuffer c,
    VkPipelineBindPoint bind, VkPipeline pipeline) {
    (void)c; (void)pipeline;
    assert(bind == VK_PIPELINE_BIND_POINT_COMPUTE);
}

static VKAPI_ATTR void VKAPI_CALL bind_sets(VkCommandBuffer c, VkPipelineBindPoint bind,
    VkPipelineLayout layout, uint32_t first, uint32_t count, const VkDescriptorSet *set,
    uint32_t offsets, const uint32_t *offset) {
    (void)c; (void)layout; (void)offset;
    assert(bind == VK_PIPELINE_BIND_POINT_COMPUTE && first == 0 && count == 1 && offsets == 0);
    assert(VALUE(*set) <= allocations && sets[VALUE(*set)].writes == 1);
    bound_set = *set;
}

static VKAPI_ATTR void VKAPI_CALL push_constants(VkCommandBuffer c, VkPipelineLayout layout,
    VkShaderStageFlags stage, uint32_t offset, uint32_t size, const void *data) {
    (void)c; (void)layout;
    assert(stage == VK_SHADER_STAGE_COMPUTE_BIT && offset == 0 && size == sizeof(pushed));
    memcpy(&pushed, data, size);
}

static VKAPI_ATTR void VKAPI_CALL dispatch(VkCommandBuffer c, uint32_t x, uint32_t y, uint32_t z) {
    (void)c;
    const struct descriptor *set = &sets[VALUE(bound_set)];
    uint32_t block_bytes = pushed.mode < 2 || pushed.mode == 4 || pushed.mode == 5 || pushed.mode == 8 ? 8 : 16;
    uint64_t bytes = (((uint64_t)pushed.height - 1) / 4 * pushed.row_blocks +
        ((uint64_t)pushed.width + 3) / 4) * block_bytes;
    uint64_t source = (uint64_t)pushed.source_word * 4;
    assert(set->buffer[0].buffer == active_image->blocks);
    assert(source <= set->buffer[0].range && bytes <= set->buffer[0].range - source);
    assert(pushed.pixels == pushed.width * pushed.height);
    assert(pushed.first_pixel <= pushed.pixels && pushed.chunk_pixels <= pushed.pixels - pushed.first_pixel);
    assert(x == (pushed.chunk_pixels + 63) / 64 && y == 1 && z == 1);
    uint64_t pixels = pushed.pixels;
    uint64_t written = pushed.mode >= 12 ? ((pixels * 6 + 3) / 4) * 4 :
        pushed.mode == 8 ? ((pixels * 3 + 3) / 4) * 4 :
        pushed.mode == 4 || pushed.mode == 5 ? ((pixels + 1) / 2) * 4 :
        pixels * (pushed.mode >= 10 ? 8 : 4);
    uint64_t destination = (uint64_t)pushed.destination_word * 4;
    assert(destination <= set->buffer[1].range && written <= set->buffer[1].range - destination);
    printf("D buffer=%" PRIu64 " source=%" PRIu64 " bytes=%" PRIu64
        " destination=%" PRIu64 " writes=%" PRIu64 " mode=%u size=%u,%u rows=%u layers=%u first=%u count=%u\n",
        VALUE(set->buffer[0].buffer), set->buffer[0].offset + source, bytes,
        set->buffer[1].offset + destination, written, pushed.mode, pushed.width, pushed.height,
        pushed.row_blocks, pushed.layer_blocks, pushed.first_pixel, pushed.chunk_pixels);
    ++dispatches;
}

static VKAPI_ATTR void VKAPI_CALL copy_to_image(VkCommandBuffer c, VkBuffer buffer,
    VkImage image, VkImageLayout layout, uint32_t count, const VkBufferImageCopy *copy) {
    (void)c;
    assert(buffer == HANDLE(VkBuffer, 50) && image == active_image->image);
    assert(count == 1 && layout == VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    assert(copy->bufferOffset == 0 && copy->bufferRowLength == 0 && copy->bufferImageHeight == 0);
    assert(copy->imageSubresource.layerCount == 1 && copy->imageExtent.depth == 1);
    printf("C image=%" PRIu64 " mip=%u layer=%u offset=%d,%d,%d size=%u,%u,%u\n",
        VALUE(image), copy->imageSubresource.mipLevel, copy->imageSubresource.baseArrayLayer,
        copy->imageOffset.x, copy->imageOffset.y, copy->imageOffset.z,
        copy->imageExtent.width, copy->imageExtent.height, copy->imageExtent.depth);
    ++copies;
}

static VKAPI_ATTR void VKAPI_CALL destroy_pool(VkDevice d, VkDescriptorPool p, const VkAllocationCallbacks *a) {
    (void)d; (void)p; (void)a;
}
static VKAPI_ATTR void VKAPI_CALL destroy_buffer(VkDevice d, VkBuffer p, const VkAllocationCallbacks *a) {
    (void)d; (void)p; (void)a;
}
static VKAPI_ATTR void VKAPI_CALL free_memory(VkDevice d, VkDeviceMemory p, const VkAllocationCallbacks *a) {
    (void)d; (void)p; (void)a;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL resolve(VkDevice d, const char *name) {
    (void)d;
#define MAP(api, fn) if (!strcmp(name, "vk" #api)) return (PFN_vkVoidFunction)fn
    MAP(CreateDescriptorPool, create_pool);
    MAP(AllocateDescriptorSets, allocate_sets);
    MAP(UpdateDescriptorSets, update_sets);
    MAP(CmdPipelineBarrier, pipeline_barrier);
    MAP(CmdBindPipeline, bind_pipeline);
    MAP(CmdBindDescriptorSets, bind_sets);
    MAP(CmdPushConstants, push_constants);
    MAP(CmdDispatch, dispatch);
    MAP(CmdCopyBufferToImage, copy_to_image);
    MAP(DestroyDescriptorPool, destroy_pool);
    MAP(DestroyBuffer, destroy_buffer);
    MAP(FreeMemory, free_memory);
    fprintf(stderr, "unexpected resolver call: %s\n", name);
    abort();
}

struct test_case {
    const char *name;
    VkFormat format;
    uint32_t width, height, mips, layers, mip, layer, layer_count, x, y, w, h;
    uint32_t limit;
    unsigned rgb_mask, consecutive;
};

int main(void) {
    const struct test_case cases[] = {
        {"bc7-4k", VK_FORMAT_BC7_UNORM_BLOCK, 4096,4096,1,1, 0,0,1, 0,0,4096,4096, 134217728,0,0},
        {"bc7-mip-layer-subrect", VK_FORMAT_BC7_UNORM_BLOCK, 4096,2048,4,3, 2,1,2, 4,8,1020,504, 134217728,0,0},
        {"bc1-odd-edge", VK_FORMAT_BC1_RGBA_UNORM_BLOCK, 2051,1027,1,3, 0,1,2, 4,8,2047,1019, 134217728,0,0},
        {"bc7-wide", VK_FORMAT_BC7_UNORM_BLOCK, 8195,12,1,1, 0,0,1, 4,4,8191,8, 134217728,0,0},
        {"bc6h-rgba-wide", VK_FORMAT_BC6H_UFLOAT_BLOCK, 8195,12,1,2, 0,1,1, 4,4,8191,8, 134217728,0,0},
        {"bc6h-rgb-wide", VK_FORMAT_BC6H_UFLOAT_BLOCK, 8195,12,1,2, 0,1,1, 4,4,8191,8, 134217728,4,0},
        {"bc1-rgb-odd", VK_FORMAT_BC1_RGB_UNORM_BLOCK, 9,7,1,3, 0,0,3, 0,0,9,7, 134217728,1,0},
        {"bc4-odd", VK_FORMAT_BC4_SNORM_BLOCK, 9,7,1,3, 0,0,3, 0,0,9,7, 134217728,0,0},
        {"limit-exact", VK_FORMAT_BC7_UNORM_BLOCK, 16384,8192,1,1, 0,0,1, 16320,8184,64,8, 134217728,0,0},
        {"limit-one-block-over", VK_FORMAT_BC7_UNORM_BLOCK, 16388,8192,1,1, 0,0,1, 16324,8184,64,8, 134250480,0,0},
        {"source-over-4gib", VK_FORMAT_BC7_UNORM_BLOCK, 16384,16384,4,20, 3,18,2, 1968,2036,80,12, 134217728,0,0},
        {"consecutive-regions", VK_FORMAT_BC7_UNORM_BLOCK, 4096,2048,4,3, 2,1,2, 4,8,1020,504, 134217728,0,1},
    };
    setbuf(stdout, NULL);
    for (unsigned n = 0; n < sizeof(cases) / sizeof(cases[0]); ++n) {
        const struct test_case *test = &cases[n];
        memset(sets, 0, sizeof(sets));
        allocations = updates = pools = dispatches = copies = barriers = 0;
        limits = (VkPhysicalDeviceLimits){.maxStorageBufferRange = test->limit,
            .minStorageBufferOffsetAlignment = 256, .maxComputeWorkGroupCount = {65535,65535,65535},
            .maxComputeWorkGroupSize = {1024,1024,64}, .maxComputeWorkGroupInvocations = 1024};
        struct hybris_bc_decoder decoder = {.device = HANDLE(VkDevice, 1), .resolver = resolve,
            .descriptor_layout = HANDLE(VkDescriptorSetLayout, 2), .layout = HANDLE(VkPipelineLayout, 3),
            .pipeline = HANDLE(VkPipeline, 4)};
        VkPhysicalDeviceMemoryProperties memory = {0};
        struct hybris_bc_transfer transfer;
        hybris_bc_transfer_init(&transfer, &decoder, &memory, &limits);
        transfer.scratch = HANDLE(VkBuffer, 50);
        scratch_size = transfer.scratch_size = test->limit < 65536 ? test->limit : 65536;
        struct hybris_bc_command_state state = {0};
        for (unsigned invocation = 0; invocation <= test->consecutive; ++invocation) {
            VkImageCreateInfo info = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                .imageType = VK_IMAGE_TYPE_2D, .format = test->format,
                .extent = {test->width,test->height,1}, .mipLevels = test->mips, .arrayLayers = test->layers,
                .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
                .usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT};
            struct hybris_bc_image image;
            assert(hybris_bc_image_describe(&info, &image) == VK_SUCCESS);
            image.blocks = HANDLE(VkBuffer, 100 + invocation);
            image.image = HANDLE(VkImage, 200 + invocation);
            image.decoded_format = hybris_bc_image_format(info.format, test->rgb_mask);
            active_image = &image;
            VkBufferImageCopy region = {
                .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, test->mip, test->layer, test->layer_count},
                .imageOffset = {(int32_t)test->x,(int32_t)test->y,0},
                .imageExtent = {test->w,test->h,1}};
            if (invocation) {
                region.imageSubresource = (VkImageSubresourceLayers){VK_IMAGE_ASPECT_COLOR_BIT,3,0,1};
                region.imageOffset = (VkOffset3D){8,4,0};
                region.imageExtent = (VkExtent3D){504,252,1};
            }
            if (!strcmp(test->name, "limit-exact")) assert(image.block_size == test->limit);
            if (!strcmp(test->name, "limit-one-block-over")) assert(image.block_size == (uint64_t)test->limit + 16);
            printf("CASE name=%s invocation=%u size=%" PRIu64 " max_range=%u\n",
                test->name, invocation, image.block_size, test->limit);
            assert(hybris_bc_decode_image(&transfer, HANDLE(VkCommandBuffer, 10), &state,
                &image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &region) == VK_SUCCESS);
            assert(state.error == VK_SUCCESS);
        }
        printf("COUNTS name=%s sets=%u updates=%u pools=%u dispatches=%u copies=%u barriers=%u\n",
            test->name, allocations, updates, pools, dispatches, copies, barriers);
        hybris_bc_transfer_finish(&transfer);
    }
    return 0;
}
