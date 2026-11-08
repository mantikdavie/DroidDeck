#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "shaders.h"

#define WIDTH 64
#define HEIGHT 32
#define FUNCTIONS(X) \
    X(vkDestroyInstance) X(vkEnumeratePhysicalDevices) \
    X(vkGetPhysicalDeviceProperties) X(vkGetPhysicalDeviceFeatures2) \
    X(vkGetPhysicalDeviceQueueFamilyProperties) X(vkGetPhysicalDeviceMemoryProperties) \
    X(vkCreateDevice) X(vkDestroyDevice) X(vkGetDeviceQueue) \
    X(vkCreateCommandPool) X(vkDestroyCommandPool) X(vkAllocateCommandBuffers) \
    X(vkResetCommandBuffer) X(vkBeginCommandBuffer) X(vkEndCommandBuffer) \
    X(vkQueueSubmit) X(vkQueueWaitIdle) \
    X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) \
    X(vkBindBufferMemory) X(vkAllocateMemory) X(vkFreeMemory) \
    X(vkMapMemory) X(vkUnmapMemory) \
    X(vkFlushMappedMemoryRanges) X(vkInvalidateMappedMemoryRanges) \
    X(vkCreateImage) X(vkDestroyImage) X(vkGetImageMemoryRequirements) \
    X(vkBindImageMemory) X(vkCreateImageView) X(vkDestroyImageView) \
    X(vkCreateShaderModule) X(vkDestroyShaderModule) \
    X(vkCreatePipelineLayout) X(vkDestroyPipelineLayout) \
    X(vkCreateGraphicsPipelines) X(vkDestroyPipeline) \
    X(vkCmdPipelineBarrier) X(vkCmdBeginRendering) X(vkCmdEndRendering) \
    X(vkCmdBindPipeline) X(vkCmdBindVertexBuffers) X(vkCmdBindIndexBuffer) \
    X(vkCmdDrawIndexed) X(vkCmdCopyImageToBuffer)
#define DECLARE(name) static PFN_##name name;
FUNCTIONS(DECLARE)
#define CHECK(call) do { \
    VkResult result_ = (call); \
    if (result_ != VK_SUCCESS) { \
        fprintf(stderr, "ERROR %s returned %d at line %d\n", #call, result_, __LINE__); \
        exit(2); \
    } \
} while (0)

static VkDevice device;
static VkPhysicalDeviceMemoryProperties memory_properties;

struct buffer {
    VkBuffer handle;
    VkDeviceMemory memory;
    void *mapped;
};

static uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags flags) {
    for (uint32_t i = 0; i < memory_properties.memoryTypeCount; ++i) {
        if ((bits & (1u << i)) &&
            (memory_properties.memoryTypes[i].propertyFlags & flags) == flags)
            return i;
    }
    fprintf(stderr, "ERROR no compatible memory type for flags 0x%x\n", flags);
    exit(2);
}

static struct buffer create_buffer(VkDeviceSize size, VkBufferUsageFlags usage) {
    struct buffer buffer = {0};
    VkBufferCreateInfo info = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = size, .usage = usage, .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    CHECK(vkCreateBuffer(device, &info, NULL, &buffer.handle));
    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(device, buffer.handle, &requirements);
    VkMemoryAllocateInfo allocation = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size,
        .memoryTypeIndex = memory_type(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT),
    };
    CHECK(vkAllocateMemory(device, &allocation, NULL, &buffer.memory));
    CHECK(vkBindBufferMemory(device, buffer.handle, buffer.memory, 0));
    CHECK(vkMapMemory(device, buffer.memory, 0, VK_WHOLE_SIZE, 0, &buffer.mapped));
    return buffer;
}

static void destroy_buffer(struct buffer buffer) {
    vkUnmapMemory(device, buffer.memory);
    vkDestroyBuffer(device, buffer.handle, NULL);
    vkFreeMemory(device, buffer.memory, NULL);
}

static VkShaderModule create_shader(const uint32_t *words, size_t size) {
    VkShaderModuleCreateInfo info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = size, .pCode = words,
    };
    VkShaderModule shader;
    CHECK(vkCreateShaderModule(device, &info, NULL, &shader));
    return shader;
}

static void image_barrier(VkCommandBuffer command, VkImage image,
                          VkImageLayout old_layout, VkImageLayout new_layout,
                          VkAccessFlags source_access, VkAccessFlags destination_access,
                          VkPipelineStageFlags source_stage, VkPipelineStageFlags destination_stage) {
    VkImageMemoryBarrier barrier = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask = source_access, .dstAccessMask = destination_access,
        .oldLayout = old_layout, .newLayout = new_layout,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image,
        .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
    };
    vkCmdPipelineBarrier(command, source_stage, destination_stage, 0,
                         0, NULL, 0, NULL, 1, &barrier);
}

static VkPipeline create_pipeline(VkPipelineLayout layout, VkShaderModule vertex,
                                  VkShaderModule fragment) {
    VkPipelineShaderStageCreateInfo stages[] = {
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = vertex, .pName = "main"},
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = fragment, .pName = "main"},
    };
    VkVertexInputBindingDescription binding = {0, 5 * sizeof(float), VK_VERTEX_INPUT_RATE_VERTEX};
    VkVertexInputAttributeDescription attributes[] = {
        {0, 0, VK_FORMAT_R32G32B32_SFLOAT, 0},
        {1, 0, VK_FORMAT_R32G32_SFLOAT, 3 * sizeof(float)},
    };
    VkPipelineVertexInputStateCreateInfo input = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
        .vertexBindingDescriptionCount = 1, .pVertexBindingDescriptions = &binding,
        .vertexAttributeDescriptionCount = 2, .pVertexAttributeDescriptions = attributes,
    };
    VkPipelineInputAssemblyStateCreateInfo assembly = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
        .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST,
    };
    VkViewport viewport = {0, HEIGHT, WIDTH, -HEIGHT, 0, 1};
    VkRect2D scissor = {{0, 0}, {WIDTH, HEIGHT}};
    VkPipelineViewportStateCreateInfo viewport_state = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1, .pViewports = &viewport,
        .scissorCount = 1, .pScissors = &scissor,
    };
    VkPipelineRasterizationStateCreateInfo rasterization = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode = VK_POLYGON_MODE_FILL, .cullMode = VK_CULL_MODE_NONE,
        .frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE, .lineWidth = 1,
    };
    VkPipelineMultisampleStateCreateInfo multisample = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT,
    };
    VkPipelineColorBlendAttachmentState attachment = {.colorWriteMask = 15};
    VkPipelineColorBlendStateCreateInfo blend = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount = 1, .pAttachments = &attachment,
    };
    VkFormat format = VK_FORMAT_R8G8B8A8_UNORM;
    VkPipelineRenderingCreateInfo rendering = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,
        .colorAttachmentCount = 1, .pColorAttachmentFormats = &format,
    };
    VkGraphicsPipelineCreateInfo info = {
        .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, .pNext = &rendering,
        .stageCount = 2, .pStages = stages,
        .pVertexInputState = &input, .pInputAssemblyState = &assembly,
        .pViewportState = &viewport_state, .pRasterizationState = &rasterization,
        .pMultisampleState = &multisample, .pColorBlendState = &blend, .layout = layout,
    };
    VkPipeline pipeline;
    CHECK(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &info, NULL, &pipeline));
    return pipeline;
}

int main(int argc, char **argv) {
    const char *selected = NULL;
    if (argc == 3 && !strcmp(argv[1], "--case")) selected = argv[2];
    else if (argc != 1) {
        fprintf(stderr, "usage: %s [--case original|same_name|unique_names|no_names]\n", argv[0]);
        return 2;
    }
    setbuf(stdout, NULL);
    alarm(60);
    void *loader = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!loader) { fprintf(stderr, "ERROR Vulkan loader: %s\n", dlerror()); return 2; }
    PFN_vkGetInstanceProcAddr get = (PFN_vkGetInstanceProcAddr)dlsym(loader, "vkGetInstanceProcAddr");
    if (!get) { fprintf(stderr, "ERROR missing vkGetInstanceProcAddr\n"); return 2; }
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)get(VK_NULL_HANDLE, "vkCreateInstance");
    if (!create) return 2;
    VkApplicationInfo application = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "DroidDeck Vulkan debug-name regression", .apiVersion = VK_API_VERSION_1_3,
    };
    VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &application,
    };
    VkInstance instance;
    CHECK(create(&instance_info, NULL, &instance));
#define LOAD(name) name = (PFN_##name)get(instance, #name); \
    if (!name) { fprintf(stderr, "ERROR missing %s\n", #name); return 2; }
    FUNCTIONS(LOAD)
    uint32_t count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &count, NULL));
    if (!count) { fprintf(stderr, "ERROR no Vulkan device\n"); return 2; }
    VkPhysicalDevice *physical_devices = calloc(count, sizeof(*physical_devices));
    if (!physical_devices) return 2;
    CHECK(vkEnumeratePhysicalDevices(instance, &count, physical_devices));
    VkPhysicalDevice physical = physical_devices[0];
    free(physical_devices);
    VkPhysicalDeviceProperties properties;
    vkGetPhysicalDeviceProperties(physical, &properties);
    printf("DEVICE name=%s vendor=0x%x api=%u\n", properties.deviceName,
           properties.vendorID, properties.apiVersion);
    VkPhysicalDeviceVulkan13Features queried = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceFeatures2 features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &queried,
    };
    vkGetPhysicalDeviceFeatures2(physical, &features);
    if (properties.apiVersion < VK_API_VERSION_1_3 || !queried.dynamicRendering) {
        fprintf(stderr, "ERROR Vulkan 1.3 dynamic rendering is required\n"); return 2;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, NULL);
    VkQueueFamilyProperties *families = calloc(count, sizeof(*families));
    if (!families) return 2;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &count, families);
    uint32_t family = 0;
    while (family < count && !(families[family].queueFlags & VK_QUEUE_GRAPHICS_BIT)) ++family;
    free(families);
    if (family == count) { fprintf(stderr, "ERROR no graphics queue\n"); return 2; }
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority,
    };
    VkPhysicalDeviceVulkan13Features enabled = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .dynamicRendering = VK_TRUE,
    };
    VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
    };
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);
    vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);

    const float vertices[][5] = {
        {-1, -1, 0, 0, 0}, {1, -1, 0, 1, 0},
        {1, 1, 0, 1, 1}, {-1, 1, 0, 0, 1},
    };
    const uint16_t indices[] = {0, 1, 2, 2, 3, 0};
    struct buffer vertex_buffer = create_buffer(sizeof(vertices) + sizeof(indices),
        VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT);
    memcpy(vertex_buffer.mapped, vertices, sizeof(vertices));
    memcpy((unsigned char *)vertex_buffer.mapped + sizeof(vertices), indices, sizeof(indices));
    VkMappedMemoryRange vertex_range = {
        .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
        .memory = vertex_buffer.memory, .size = VK_WHOLE_SIZE,
    };
    CHECK(vkFlushMappedMemoryRanges(device, 1, &vertex_range));
    struct buffer readback = create_buffer(WIDTH * HEIGHT * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
    VkImageCreateInfo image_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D,
        .format = VK_FORMAT_R8G8B8A8_UNORM, .extent = {WIDTH, HEIGHT, 1},
        .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    VkImage image;
    CHECK(vkCreateImage(device, &image_info, NULL, &image));
    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(device, image, &requirements);
    VkMemoryAllocateInfo image_allocation = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = requirements.size,
        .memoryTypeIndex = memory_type(requirements.memoryTypeBits, 0),
    };
    VkDeviceMemory image_memory;
    CHECK(vkAllocateMemory(device, &image_allocation, NULL, &image_memory));
    CHECK(vkBindImageMemory(device, image, image_memory, 0));
    VkImageViewCreateInfo view_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = image,
        .viewType = VK_IMAGE_VIEW_TYPE_2D, .format = image_info.format,
        .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
    };
    VkImageView view;
    CHECK(vkCreateImageView(device, &view_info, NULL, &view));
    VkCommandPoolCreateInfo pool_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = family,
    };
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    VkCommandBufferAllocateInfo command_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1,
    };
    VkCommandBuffer command;
    CHECK(vkAllocateCommandBuffers(device, &command_info, &command));
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModule fragment = create_shader(shader_fragment, sizeof(shader_fragment));
    const struct {
        const char *name;
        const uint32_t *words;
        size_t size;
    } variants[] = {
        {"original", shader_original, sizeof(shader_original)},
        {"same_name", shader_same_name, sizeof(shader_same_name)},
        {"unique_names", shader_unique_names, sizeof(shader_unique_names)},
        {"no_names", shader_no_names, sizeof(shader_no_names)},
    };
    unsigned cases = 0, failed = 0, total_bad = 0;
    for (unsigned variant = 0; variant < sizeof(variants) / sizeof(variants[0]); ++variant) {
        if (selected && strcmp(selected, variants[variant].name)) continue;
        VkShaderModule vertex = create_shader(variants[variant].words, variants[variant].size);
        VkPipeline pipeline = create_pipeline(layout, vertex, fragment);
        CHECK(vkResetCommandBuffer(command, 0));
        VkCommandBufferBeginInfo begin = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
            .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
        };
        CHECK(vkBeginCommandBuffer(command, &begin));
        image_barrier(command, image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
                      0, VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
                      VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT);
        VkRenderingAttachmentInfo attachment = {
            .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
            .imageView = view, .imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
            .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
            .clearValue.color = {{0, 0, 1, 0}},
        };
        VkRenderingInfo rendering = {
            .sType = VK_STRUCTURE_TYPE_RENDERING_INFO, .renderArea = {{0, 0}, {WIDTH, HEIGHT}},
            .layerCount = 1, .colorAttachmentCount = 1, .pColorAttachments = &attachment,
        };
        vkCmdBeginRendering(command, &rendering);
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
        VkDeviceSize offset = 0;
        vkCmdBindVertexBuffers(command, 0, 1, &vertex_buffer.handle, &offset);
        vkCmdBindIndexBuffer(command, vertex_buffer.handle, sizeof(vertices), VK_INDEX_TYPE_UINT16);
        vkCmdDrawIndexed(command, 6, 1, 0, 0, 0);
        vkCmdEndRendering(command);
        image_barrier(command, image, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                      VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT,
                      VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT);
        VkBufferImageCopy copy = {
            .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
            .imageExtent = {WIDTH, HEIGHT, 1},
        };
        vkCmdCopyImageToBuffer(command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.handle, 1, &copy);
        VkBufferMemoryBarrier host_barrier = {
            .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = readback.handle, .size = VK_WHOLE_SIZE,
        };
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                             0, 0, NULL, 1, &host_barrier, 0, NULL);
        CHECK(vkEndCommandBuffer(command));
        VkSubmitInfo submit = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount = 1, .pCommandBuffers = &command,
        };
        CHECK(vkQueueSubmit(queue, 1, &submit, VK_NULL_HANDLE));
        CHECK(vkQueueWaitIdle(queue));
        VkMappedMemoryRange range = {
            .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
            .memory = readback.memory, .size = VK_WHOLE_SIZE,
        };
        CHECK(vkInvalidateMappedMemoryRanges(device, 1, &range));
        const unsigned char *pixels = readback.mapped;
        unsigned bad = 0;
        for (unsigned y = 0; y < HEIGHT; ++y) for (unsigned x = 0; x < WIDTH; ++x) {
            const unsigned char *pixel = pixels + (y * WIDTH + x) * 4;
            float expected[] = {(x + 0.5f) / WIDTH, (y + 0.5f) / HEIGHT, 0, 1};
            int mismatch = 0;
            for (unsigned component = 0; component < 4; ++component)
                mismatch |= fabsf(pixel[component] / 255.0f - expected[component]) > 1.01f / 255;
            if (mismatch) {
                if (bad < 2)
                    printf("MISMATCH case=%s xy=%u,%u rgba=%u,%u,%u,%u expected_rg=%.6f,%.6f\n",
                           variants[variant].name, x, y, pixel[0], pixel[1], pixel[2], pixel[3], expected[0], expected[1]);
                ++bad;
            }
        }
        printf("CASE name=%s pixels=%u bad=%u result=%s\n", variants[variant].name,
               WIDTH * HEIGHT, bad, bad ? "FAIL" : "PASS");
        ++cases;
        failed += bad != 0;
        total_bad += bad;
        vkDestroyPipeline(device, pipeline, NULL);
        vkDestroyShaderModule(device, vertex, NULL);
    }
    printf("SUMMARY cases=%u failed_cases=%u mismatched_pixels=%u\n", cases, failed, total_bad);
    vkDestroyShaderModule(device, fragment, NULL);
    vkDestroyPipelineLayout(device, layout, NULL);
    vkDestroyCommandPool(device, pool, NULL);
    vkDestroyImageView(device, view, NULL);
    vkDestroyImage(device, image, NULL);
    vkFreeMemory(device, image_memory, NULL);
    destroy_buffer(readback);
    destroy_buffer(vertex_buffer);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    dlclose(loader);
    if (!cases) { fprintf(stderr, "ERROR unknown case: %s\n", selected); return 2; }
    return failed ? 1 : 0;
}
