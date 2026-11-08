#version 450
layout(location = 0) in vec3 position;
layout(location = 1) in vec2 uv_in;
layout(location = 0) out vec2 uv_out;

void main() {
    uv_out = vec2(uv_in.x, 1.0 - uv_in.y);
    gl_Position = vec4(position, 1.0);
}
