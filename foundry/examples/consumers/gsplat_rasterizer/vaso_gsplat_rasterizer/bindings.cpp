#include <torch/extension.h>

#include <stdexcept>
#include <string>
#include <tuple>

#include "Common.h"
#include "Ops.h"

namespace py = pybind11;

namespace {

gsplat::CameraModelType parse_camera_model(const std::string &camera_model) {
    if (camera_model == "pinhole") {
        return gsplat::CameraModelType::PINHOLE;
    }
    if (camera_model == "ortho") {
        return gsplat::CameraModelType::ORTHO;
    }
    if (camera_model == "fisheye") {
        return gsplat::CameraModelType::FISHEYE;
    }
    throw std::invalid_argument("unsupported camera model: " + camera_model);
}

std::tuple<at::Tensor, at::Tensor> project_ewa_simple_forward(
    const at::Tensor means,
    const at::Tensor covars,
    const at::Tensor intrinsics,
    const uint32_t image_width,
    const uint32_t image_height,
    const std::string &camera_model
) {
    return gsplat::projection_ewa_simple_fwd(
        means,
        covars,
        intrinsics,
        image_width,
        image_height,
        parse_camera_model(camera_model)
    );
}

std::tuple<at::Tensor, at::Tensor> project_ewa_simple_backward(
    const at::Tensor means,
    const at::Tensor covars,
    const at::Tensor intrinsics,
    const uint32_t image_width,
    const uint32_t image_height,
    const std::string &camera_model,
    const at::Tensor v_means2d,
    const at::Tensor v_covars2d
) {
    return gsplat::projection_ewa_simple_bwd(
        means,
        covars,
        intrinsics,
        image_width,
        image_height,
        parse_camera_model(camera_model),
        v_means2d,
        v_covars2d
    );
}

std::tuple<at::Tensor, at::Tensor, at::Tensor> rasterize_to_pixels_forward(
    const at::Tensor means2d,
    const at::Tensor conics,
    const at::Tensor colors,
    const at::Tensor opacities,
    const at::optional<at::Tensor> backgrounds,
    const at::optional<at::Tensor> masks,
    const uint32_t image_width,
    const uint32_t image_height,
    const uint32_t tile_size,
    const at::Tensor tile_offsets,
    const at::Tensor flatten_ids
) {
    return gsplat::rasterize_to_pixels_3dgs_fwd(
        means2d,
        conics,
        colors,
        opacities,
        backgrounds,
        masks,
        image_width,
        image_height,
        tile_size,
        tile_offsets,
        flatten_ids
    );
}

std::tuple<at::Tensor, at::Tensor, at::Tensor, at::Tensor, at::Tensor>
rasterize_to_pixels_backward(
    const at::Tensor means2d,
    const at::Tensor conics,
    const at::Tensor colors,
    const at::Tensor opacities,
    const at::optional<at::Tensor> backgrounds,
    const at::optional<at::Tensor> masks,
    const uint32_t image_width,
    const uint32_t image_height,
    const uint32_t tile_size,
    const at::Tensor tile_offsets,
    const at::Tensor flatten_ids,
    const at::Tensor render_alphas,
    const at::Tensor last_ids,
    const at::Tensor v_render_colors,
    const at::Tensor v_render_alphas,
    const bool absgrad
) {
    return gsplat::rasterize_to_pixels_3dgs_bwd(
        means2d,
        conics,
        colors,
        opacities,
        backgrounds,
        masks,
        image_width,
        image_height,
        tile_size,
        tile_offsets,
        flatten_ids,
        render_alphas,
        last_ids,
        v_render_colors,
        v_render_alphas,
        absgrad
    );
}

} // namespace

PYBIND11_MODULE(_C, m) {
    m.def("project_ewa_simple_forward", &project_ewa_simple_forward);
    m.def("project_ewa_simple_backward", &project_ewa_simple_backward);
    m.def("intersect_tile", &gsplat::intersect_tile);
    m.def("intersect_offset", &gsplat::intersect_offset);
    m.def("rasterize_to_pixels_forward", &rasterize_to_pixels_forward);
    m.def("rasterize_to_pixels_backward", &rasterize_to_pixels_backward);
}
