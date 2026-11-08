#define _POSIX_C_SOURCE 200809L
#define VK_NO_PROTOTYPES
#include <assert.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "bc_context.c"
#include "bc_commands.c"
#include "bc_resources.c"

#define HANDLE(type, value) ((type)(uintptr_t)(value))
struct allocation_state {
    atomic_int live, allocations, frees, fail_after;
};
struct allocation_header {
    max_align_t alignment;
    struct allocation_state *owner;
    uint64_t magic;
};
static atomic_uint retired_states, retired_transfers, decoder_destroys, image_destroys;
static atomic_uint native_allocations, native_image_creates;
static _Thread_local VkResult native_result, decoder_result;
static _Thread_local uint64_t next_command = UINT64_C(0x100000000);
static _Thread_local uint64_t next_image = UINT64_C(0x200000000);
static _Thread_local const void *expected_image_allocator;

static void *VKAPI_CALL tracked_allocate(void *user, size_t size, size_t alignment,
    VkSystemAllocationScope scope)
{
    struct allocation_state *state = user;
    assert(alignment == _Alignof(max_align_t) && scope == VK_SYSTEM_ALLOCATION_SCOPE_DEVICE);
    int left = atomic_load(&state->fail_after);
    if (left == 0) return NULL;
    if (left > 0) atomic_fetch_sub(&state->fail_after, 1);
    struct allocation_header *header = malloc(sizeof(*header) + size);
    assert(header);
    header->owner = state;
    header->magic = UINT64_C(0x1b319dbc725e8a10);
    atomic_fetch_add(&state->live, 1);
    atomic_fetch_add(&state->allocations, 1);
    memset(header + 1, 0xcd, size);
    return header + 1;
}
static void VKAPI_CALL tracked_free(void *user, void *memory)
{
    if (!memory) return;
    struct allocation_header *header = (struct allocation_header *)memory - 1;
    struct allocation_state *state = user;
    assert(header->owner == state && header->magic == UINT64_C(0x1b319dbc725e8a10));
    header->magic = 0;
    assert(atomic_fetch_sub(&state->live, 1) > 0);
    atomic_fetch_add(&state->frees, 1);
    free(header);
}
static VkAllocationCallbacks callbacks(struct allocation_state *state)
{
    atomic_store(&state->fail_after, -1);
    return (VkAllocationCallbacks){.pUserData = state, .pfnAllocation = tracked_allocate,
        .pfnFree = tracked_free};
}
void hybris_bc_layouts_init(struct hybris_bc_layouts *layouts, VkDevice device,
    PFN_vkGetDeviceProcAddr resolver)
{
    *layouts = (struct hybris_bc_layouts){.device = device, .resolver = resolver};
    assert(!pthread_mutex_init(&layouts->guard, NULL));
}
void hybris_bc_layouts_finish(struct hybris_bc_layouts *layouts)
{ assert(!pthread_mutex_destroy(&layouts->guard)); }
VkResult hybris_bc_decoder_create(VkDevice device, PFN_vkGetDeviceProcAddr resolver,
    const VkAllocationCallbacks *allocator, struct hybris_bc_decoder *decoder)
{
    assert(!allocator);
    if (decoder_result != VK_SUCCESS) return decoder_result;
    *decoder = (struct hybris_bc_decoder){.device = device, .resolver = resolver};
    return VK_SUCCESS;
}
void hybris_bc_decoder_destroy(struct hybris_bc_decoder *decoder, const VkAllocationCallbacks *allocator)
{ assert(decoder->device && !allocator); atomic_fetch_add(&decoder_destroys, 1); }
void hybris_bc_command_state_init(struct hybris_bc_command_state *state, struct hybris_bc_layouts *layouts)
{ *state = (struct hybris_bc_command_state){.layouts = layouts}; }
void hybris_bc_command_state_reset(struct hybris_bc_command_state *state)
{ atomic_fetch_add(&retired_states, 1); hybris_bc_command_state_init(state, state->layouts); }
void hybris_bc_transfer_init(struct hybris_bc_transfer *transfer, const struct hybris_bc_decoder *decoder,
    const VkPhysicalDeviceMemoryProperties *memory, const VkPhysicalDeviceLimits *limits)
{ *transfer = (struct hybris_bc_transfer){.decoder = decoder, .memory = memory, .limits = limits}; }
void hybris_bc_transfer_finish(struct hybris_bc_transfer *transfer)
{
    assert(transfer->decoder->device);
    atomic_fetch_add(&retired_transfers, 1);
    hybris_bc_transfer_init(transfer, transfer->decoder, transfer->memory, transfer->limits);
}
VkResult hybris_bc_image_create(VkDevice device, PFN_vkGetDeviceProcAddr resolver,
    const VkPhysicalDeviceMemoryProperties *memory, const VkImageCreateInfo *info,
    unsigned rgb_mask, const VkAllocationCallbacks *allocator, struct hybris_bc_image *image)
{
    (void)memory; (void)rgb_mask;
    assert((allocator ? allocator->pUserData : NULL) == expected_image_allocator);
    if (native_result != VK_SUCCESS) return native_result;
    *image = (struct hybris_bc_image){.device = device, .resolver = resolver,
        .image = HANDLE(VkImage, next_image), .format = info->format};
    next_image += 4096;
    return VK_SUCCESS;
}
void hybris_bc_image_destroy(struct hybris_bc_image *image, const VkAllocationCallbacks *allocator)
{
    assert(image->image && (allocator ? allocator->pUserData : NULL) == expected_image_allocator);
    atomic_fetch_add(&image_destroys, 1);
}
/* AddressSanitizer retains dispatch tables for hooks outside this test's scope. */
VkResult hybris_bc_layout_add(struct hybris_bc_layouts *layouts, VkPipelineLayout handle,
    const VkPipelineLayoutCreateInfo *info)
{ (void)layouts; (void)handle; (void)info; abort(); }
void hybris_bc_layout_remove(struct hybris_bc_layouts *layouts, VkPipelineLayout handle)
{ (void)layouts; (void)handle; abort(); }
VkResult hybris_bc_image_describe(const VkImageCreateInfo *info, struct hybris_bc_image *image)
{ (void)info; (void)image; abort(); }
void hybris_bc_image_backing_info(const struct hybris_bc_image *image,
    const VkImageCreateInfo *info, VkBufferCreateInfo *backing)
{ (void)image; (void)info; (void)backing; abort(); }
void hybris_bc_save_push(struct hybris_bc_command_state *state, VkPipelineLayout layout,
    VkShaderStageFlags stages, uint32_t offset, uint32_t size, const void *values)
{ (void)state; (void)layout; (void)stages; (void)offset; (void)size; (void)values; abort(); }
void hybris_bc_save_descriptors(struct hybris_bc_command_state *state,
    VkPipelineBindPoint point, VkPipelineLayout layout, uint32_t first, uint32_t count,
    const VkDescriptorSet *sets, uint32_t offset_count, const uint32_t *offsets)
{
    (void)state; (void)point; (void)layout; (void)first; (void)count;
    (void)sets; (void)offset_count; (void)offsets; abort();
}
void hybris_bc_save_pipeline(struct hybris_bc_command_state *state,
    VkPipelineBindPoint point, VkPipeline pipeline)
{ (void)state; (void)point; (void)pipeline; abort(); }
int hybris_bc_view_compatible(VkFormat image, VkFormat view)
{ (void)image; (void)view; abort(); }
VkFormat hybris_bc_image_format(VkFormat format, unsigned mask)
{ (void)format; (void)mask; abort(); }
VkResult hybris_bc_image_bind(const struct hybris_bc_image *image, VkDeviceMemory memory, VkDeviceSize offset)
{ (void)image; (void)memory; (void)offset; abort(); }
static VkResult VKAPI_CALL native_allocate(VkDevice device, const VkCommandBufferAllocateInfo *info,
    VkCommandBuffer *commands)
{
    assert(device);
    atomic_fetch_add(&native_allocations, 1);
    if (native_result != VK_SUCCESS) return native_result;
    for (uint32_t i = 0; i < info->commandBufferCount; ++i) {
        commands[i] = HANDLE(VkCommandBuffer, next_command);
        next_command += 4096;
    }
    return VK_SUCCESS;
}
static void VKAPI_CALL native_free(VkDevice device, VkCommandPool pool, uint32_t count,
    const VkCommandBuffer *commands)
{ assert(device && pool && (!count || commands)); }
static VkResult VKAPI_CALL native_begin(VkCommandBuffer command, const VkCommandBufferBeginInfo *info)
{ assert(command && info); return native_result; }
static VkResult VKAPI_CALL native_reset_command(VkCommandBuffer command, VkCommandBufferResetFlags flags)
{ assert(command && !flags); return native_result; }
static VkResult VKAPI_CALL native_reset_pool(VkDevice device, VkCommandPool pool, VkCommandPoolResetFlags flags)
{ assert(device && pool && !flags); return native_result; }
static void VKAPI_CALL native_destroy_pool(VkDevice device, VkCommandPool pool,
    const VkAllocationCallbacks *allocator)
{ assert(device && pool && !allocator); }
static VkResult VKAPI_CALL native_create_image(VkDevice device, const VkImageCreateInfo *info,
    const VkAllocationCallbacks *allocator, VkImage *out)
{
    assert(device && info && (allocator ? allocator->pUserData : NULL) == expected_image_allocator);
    atomic_fetch_add(&native_image_creates, 1);
    if (native_result != VK_SUCCESS) return native_result;
    *out = HANDLE(VkImage, next_image);
    next_image += 4096;
    return VK_SUCCESS;
}
static void VKAPI_CALL native_destroy_image(VkDevice device, VkImage image, const VkAllocationCallbacks *allocator)
{ (void)image; assert(device && (allocator ? allocator->pUserData : NULL) == expected_image_allocator); }
static PFN_vkVoidFunction VKAPI_CALL resolver(VkDevice device, const char *name)
{
    assert(device);
#define RETURN(n, f) if (!strcmp(name, "vk" #n)) return (PFN_vkVoidFunction)f
    RETURN(AllocateCommandBuffers, native_allocate); RETURN(FreeCommandBuffers, native_free);
    RETURN(BeginCommandBuffer, native_begin); RETURN(ResetCommandBuffer, native_reset_command);
    RETURN(ResetCommandPool, native_reset_pool); RETURN(DestroyCommandPool, native_destroy_pool);
    RETURN(CreateImage, native_create_image); RETURN(DestroyImage, native_destroy_image);
#undef RETURN
    fprintf(stderr, "Unexpected native resolver lookup: %s\n", name);
    abort();
}
static struct hybris_bc_device *add_device(unsigned id, unsigned mask, const VkAllocationCallbacks *allocator)
{
    VkPhysicalDeviceMemoryProperties memory = {0};
    VkPhysicalDeviceProperties properties = {0};
    VkDevice handle = HANDLE(VkDevice, id);
    assert(hybris_bc_device_add(handle, resolver, &memory, &properties, mask, 0, allocator) == VK_SUCCESS);
    struct hybris_bc_device *device = hybris_bc_device_find(handle);
    assert(device && device->handle == handle);
    return device;
}
static void allocate(struct hybris_bc_device *device, unsigned pool, unsigned count, VkCommandBuffer *out)
{
    VkCommandBufferAllocateInfo info = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = HANDLE(VkCommandPool, pool), .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = count};
    assert(allocate_commands(device->handle, &info, out) == VK_SUCCESS);
}
static struct hybris_bc_command *find_command(VkCommandBuffer handle, struct hybris_bc_device *expected)
{
    struct hybris_bc_device *owner = HANDLE(struct hybris_bc_device *, 1);
    struct hybris_bc_command *found = hybris_bc_command_find(handle, &owner);
    assert((found != NULL) == (expected != NULL) && owner == expected);
    if (found) assert(found->handle == handle);
    return found;
}
static VkImage make_image(struct hybris_bc_device *device, VkFormat format, const VkAllocationCallbacks *allocator)
{
    VkImageCreateInfo info = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = format, .extent = {16,16,1},
        .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = VK_IMAGE_USAGE_SAMPLED_BIT};
    VkImage image;
    assert(create_image(device->handle, &info, allocator, &image) == VK_SUCCESS);
    struct hybris_bc_resource *found = hybris_bc_resource_find(device, image);
    assert((found != NULL) == (device->format_mask != 0));
    if (found) assert(found->format == format && found->handle == image);
    return image;
}
static unsigned command_count(struct hybris_bc_device *device)
{
    unsigned count = 0;
    for (struct hybris_bc_command *c = device->commands; c; c = c->next) ++count;
    return count;
}
static unsigned resource_count(struct hybris_bc_device *device)
{
    unsigned count = 0;
    for (struct hybris_bc_resource *r = device->images; r; r = r->next) ++count;
    return count;
}
static void test_collisions_and_reuse(void)
{
    struct allocation_state memory = {0};
    VkAllocationCallbacks cb = callbacks(&memory);
    struct hybris_bc_device *a = add_device(1, 1u << 2, &cb), *b = add_device(2, 1u << 2, &cb);
    enum { COMMANDS = 1024, IMAGES = 2048 };
    VkCommandBuffer commands[COMMANDS], other[3];
    allocate(a, 10, COMMANDS, commands); allocate(b, 20, 3, other);
    for (unsigned i = 0; i < COMMANDS; ++i) find_command(commands[i], a);
    for (unsigned i = 0; i < 3; ++i) find_command(other[i], b);
    VkImage images[IMAGES];
    for (unsigned i = 0; i < IMAGES; ++i) images[i] = make_image(a, VK_FORMAT_R8G8B8A8_UNORM, NULL);
    next_image = (uintptr_t)images[3];
    VkImage same = make_image(b, VK_FORMAT_BC1_RGBA_UNORM_BLOCK, NULL);
    assert(same == images[3]);
    assert(hybris_bc_resource_find(a, same) != hybris_bc_resource_find(b, same));
    for (unsigned i = 0; i < IMAGES; i += 2) destroy_image(a->handle, images[i], NULL);
    for (unsigned i = 0; i < IMAGES; ++i)
        assert((hybris_bc_resource_find(a, images[i]) != NULL) == (i % 2 != 0));
    assert(hybris_bc_resource_find(b, same));
    next_image = (uintptr_t)images[0];
    assert(make_image(a, VK_FORMAT_BC1_RGBA_UNORM_BLOCK, NULL) == images[0]);
    destroy_image(a->handle, images[0], NULL);
    for (unsigned i = 1; i < IMAGES; i += 2) destroy_image(a->handle, images[i], NULL);
    assert(!resource_count(a));
    for (unsigned i = 0; i < COMMANDS; i += 2) free_commands(a->handle, HANDLE(VkCommandPool, 10), 1, &commands[i]);
    for (unsigned i = 0; i < COMMANDS; ++i) find_command(commands[i], i % 2 ? a : NULL);
    next_command = (uintptr_t)commands[0];
    VkCommandBuffer reused;
    allocate(a, 11, 1, &reused); assert(reused == commands[0]); find_command(reused, a);
    destroy_pool(a->handle, HANDLE(VkCommandPool, 10), NULL);
    assert(command_count(a) == 1); find_command(reused, a);
    destroy_pool(a->handle, HANDLE(VkCommandPool, 11), NULL); find_command(reused, NULL);
    hybris_bc_device_remove(a->handle);
    assert(!hybris_bc_device_find(HANDLE(VkDevice, 1)));
    find_command(other[0], b); assert(hybris_bc_resource_find(b, same));
    hybris_bc_device_remove(b->handle);
    find_command(other[0], NULL);
    assert(!atomic_load(&memory.live) && atomic_load(&memory.allocations) == atomic_load(&memory.frees));
    puts("PASS collisions_reuse_multidevice");
}
static void test_reset_lifetimes(void)
{
    struct hybris_bc_device *device = add_device(3, 1u << 2, NULL);
    VkCommandBuffer first[4], second[3];
    allocate(device, 31, 4, first); allocate(device, 32, 3, second);
    for (unsigned i = 0; i < 4; ++i) find_command(first[i], device)->state.error = VK_ERROR_DEVICE_LOST;
    find_command(second[0], device)->state.error = VK_ERROR_DEVICE_LOST;
    unsigned retired = atomic_load(&retired_states);
    native_result = VK_ERROR_OUT_OF_HOST_MEMORY;
    assert(reset_pool(device->handle, HANDLE(VkCommandPool, 31), 0) == native_result);
    assert(atomic_load(&retired_states) == retired && command_count(device) == 7);
    for (unsigned i = 0; i < 4; ++i) assert(find_command(first[i], device)->state.error == VK_ERROR_DEVICE_LOST);
    native_result = VK_SUCCESS;
    assert(reset_pool(device->handle, HANDLE(VkCommandPool, 31), 0) == VK_SUCCESS);
    assert(atomic_load(&retired_states) == retired + 4 && command_count(device) == 7);
    for (unsigned i = 0; i < 4; ++i) assert(find_command(first[i], device)->state.error == VK_SUCCESS);
    assert(find_command(second[0], device)->state.error == VK_ERROR_DEVICE_LOST);
    struct hybris_bc_command *original = find_command(first[0], device);
    original->state.error = VK_ERROR_DEVICE_LOST;
    VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    native_result = VK_ERROR_OUT_OF_HOST_MEMORY;
    assert(begin_command(first[0], &begin) == native_result && reset_command(first[0], 0) == native_result);
    assert(original->state.error == VK_ERROR_DEVICE_LOST && find_command(first[0], device) == original);
    native_result = VK_SUCCESS;
    assert(begin_command(first[0], &begin) == VK_SUCCESS && original->state.error == VK_SUCCESS);
    original->state.error = VK_ERROR_DEVICE_LOST;
    assert(reset_command(first[0], 0) == VK_SUCCESS && original->state.error == VK_SUCCESS);
    assert(find_command(first[0], device) == original && command_count(device) == 7);
    destroy_pool(device->handle, HANDLE(VkCommandPool, 31), NULL);
    for (unsigned i = 0; i < 4; ++i) find_command(first[i], NULL);
    for (unsigned i = 0; i < 3; ++i) find_command(second[i], device);
    hybris_bc_device_remove(device->handle);
    puts("PASS reset_pool_command_lifetimes");
}
static void test_failure_and_allocators(void)
{
    struct allocation_state memory = {0}, image_memory = {0};
    VkAllocationCallbacks cb = callbacks(&memory), image_cb = callbacks(&image_memory);
    VkPhysicalDeviceMemoryProperties mp = {0}; VkPhysicalDeviceProperties props = {0};
    atomic_store(&memory.fail_after, 0);
    assert(hybris_bc_device_add(HANDLE(VkDevice,4), resolver, &mp, &props, 1u<<2, 0, &cb) == VK_ERROR_OUT_OF_HOST_MEMORY);
    assert(!hybris_bc_device_find(HANDLE(VkDevice,4)) && !atomic_load(&memory.live));
    atomic_store(&memory.fail_after, -1); decoder_result = VK_ERROR_OUT_OF_DEVICE_MEMORY;
    assert(hybris_bc_device_add(HANDLE(VkDevice,4), resolver, &mp, &props, 1u<<2, 0, &cb) == decoder_result);
    assert(!hybris_bc_device_find(HANDLE(VkDevice,4)) && !atomic_load(&memory.live));
    decoder_result = VK_SUCCESS;
    struct hybris_bc_device *device = add_device(4, 1u << 2, &cb);
    VkCommandBuffer handles[5];
    VkCommandBufferAllocateInfo info = {.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool=HANDLE(VkCommandPool,41),.commandBufferCount=5};
    unsigned calls = atomic_load(&native_allocations);
    atomic_store(&memory.fail_after, 2);
    assert(allocate_commands(device->handle,&info,handles) == VK_ERROR_OUT_OF_HOST_MEMORY);
    assert(atomic_load(&native_allocations) == calls && !command_count(device) && atomic_load(&memory.live) == 1);
    atomic_store(&memory.fail_after,-1); native_result = VK_ERROR_OUT_OF_DEVICE_MEMORY;
    assert(allocate_commands(device->handle,&info,handles) == native_result);
    assert(!command_count(device) && atomic_load(&memory.live) == 1);
    VkImageCreateInfo image_info = {.sType=VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,.format=VK_FORMAT_R8G8B8A8_UNORM};
    VkImage image;
    assert(create_image(device->handle,&image_info,NULL,&image) == native_result);
    image_info.format = VK_FORMAT_BC1_RGBA_UNORM_BLOCK;
    assert(create_image(device->handle,&image_info,NULL,&image) == native_result);
    assert(!resource_count(device) && atomic_load(&memory.live) == 1);
    native_result = VK_SUCCESS; atomic_store(&memory.fail_after,0);
    calls = atomic_load(&native_image_creates);
    assert(create_image(device->handle,&image_info,NULL,&image) == VK_ERROR_OUT_OF_HOST_MEMORY);
    assert(atomic_load(&native_image_creates) == calls && !resource_count(device));
    atomic_store(&memory.fail_after,-1);
    expected_image_allocator = &image_memory;
    make_image(device,VK_FORMAT_BC1_RGBA_UNORM_BLOCK,&image_cb);
    allocate(device,41,5,handles);
    hybris_bc_device_remove(device->handle);
    expected_image_allocator = NULL;
    assert(!atomic_load(&memory.live) && atomic_load(&memory.allocations) == atomic_load(&memory.frees));
    assert(!atomic_load(&image_memory.allocations));
    device = add_device(4,0,&cb);
    image = make_image(device,VK_FORMAT_R8G8B8A8_UNORM,NULL);
    assert(!resource_count(device)); destroy_image(device->handle,image,NULL);
    allocate(device,41,1,handles);
    unsigned transfers = atomic_load(&retired_transfers);
    hybris_bc_device_remove(device->handle);
    assert(atomic_load(&retired_transfers) == transfers && !atomic_load(&memory.live));
    find_command(handles[0],NULL);
    puts("PASS allocation_failure_custom_allocators_zero_mask");
}
struct thread_info { struct hybris_bc_device *device; unsigned id; };
static void *worker(void *data)
{
    const struct thread_info *info = data;
    next_command = UINT64_C(0x10000000000) + ((uint64_t)info->id << 32);
    next_image = UINT64_C(0x20000000000) + ((uint64_t)info->id << 32);
    unsigned pool = 100 + info->id;
    for (unsigned i = 0; i < 200; ++i) {
        VkCommandBuffer commands[3]; allocate(info->device,pool,3,commands);
        VkImage image = make_image(info->device,VK_FORMAT_R8G8B8A8_UNORM,NULL);
        for (unsigned j = 0; j < 3; ++j) find_command(commands[j],info->device);
        assert(reset_pool(info->device->handle,HANDLE(VkCommandPool,pool),0) == VK_SUCCESS);
        for (unsigned j = 0; j < 3; ++j) find_command(commands[j],info->device);
        assert(hybris_bc_resource_find(info->device,image));
        destroy_image(info->device->handle,image,NULL);
        assert(!hybris_bc_resource_find(info->device,image));
        free_commands(info->device->handle,HANDLE(VkCommandPool,pool),3,commands);
        for (unsigned j = 0; j < 3; ++j) find_command(commands[j],NULL);
    }
    return NULL;
}
static void test_threads(void)
{
    struct allocation_state memory = {0}; VkAllocationCallbacks cb = callbacks(&memory);
    struct hybris_bc_device *device = add_device(5,1u<<2,&cb);
    pthread_t threads[4]; struct thread_info info[4];
    for (unsigned i = 0; i < 4; ++i) {
        info[i] = (struct thread_info){.device=device,.id=i};
        assert(!pthread_create(&threads[i],NULL,worker,&info[i]));
    }
    for (unsigned i = 0; i < 4; ++i) assert(!pthread_join(threads[i],NULL));
    assert(!command_count(device) && !resource_count(device));
    hybris_bc_device_remove(device->handle);
    assert(!atomic_load(&memory.live) && atomic_load(&memory.allocations) == atomic_load(&memory.frees));
    puts("PASS unrelated_pool_image_threads");
}
int main(void)
{
    alarm(60); setbuf(stdout,NULL);
    test_collisions_and_reuse(); test_reset_lifetimes(); test_failure_and_allocators(); test_threads();
    assert(!devices);
    printf("SUMMARY groups=4 retired_states=%u retired_transfers=%u decoder_destroys=%u image_destroys=%u\n",
        atomic_load(&retired_states),atomic_load(&retired_transfers),atomic_load(&decoder_destroys),atomic_load(&image_destroys));
    return 0;
}
