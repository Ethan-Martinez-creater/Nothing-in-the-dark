"""P0-1.1b/c: pixel average-hash, OCR text, video keyframe similarity."""

from __future__ import annotations

from app.services.media_features import (
    average_hash,
    extract_keyframes,
    extract_ocr_text,
    hamming_distance,
    infer_media_type,
    media_items_from_post,
    pixel_phash,
    similar_phash,
)


def _gradient_grid(offset: int = 0) -> list[list[int]]:
    return [[min(255, col * 16 + offset) for col in range(8)] for _ in range(8)]


def test_average_hash_is_deterministic_and_64_bit() -> None:
    digest = average_hash(_gradient_grid())
    assert digest == average_hash(_gradient_grid())
    assert len(digest) == 16
    assert int(digest, 16) >= 0


def test_similar_images_match_distant_images_do_not() -> None:
    close = average_hash(_gradient_grid(0))
    near = average_hash(_gradient_grid(4))
    far = average_hash([[255 - col * 16 for col in range(8)] for _ in range(8)])
    assert similar_phash(close, near)
    assert not similar_phash(close, far)
    assert hamming_distance(close, close) == 0


def test_pixel_phash_from_luminance_pixels() -> None:
    # Row-major 8x8, value varies by column — same layout as _gradient_grid().
    pixels = [col * 16 for _ in range(8) for col in range(8)]
    digest = pixel_phash(width=8, height=8, pixels=pixels)
    assert digest == average_hash(_gradient_grid())


def test_extract_ocr_prefers_explicit_field_then_caption() -> None:
    post = {
        "content": "现场图写着「立即疏散」四个字",
        "image_url": "https://cdn.example.com/scene.jpg",
    }
    explicit = extract_ocr_text(post, {"ocr_text": "立即疏散"})
    inferred = extract_ocr_text(post, {})
    assert explicit == "立即疏散"
    assert "立即疏散" in inferred


def test_infer_media_type_and_video_keyframes() -> None:
    assert infer_media_type("https://cdn.example.com/a.mp4") == "video"
    assert infer_media_type("https://cdn.example.com/a.jpg") == "image"
    post = {
        "video_url": "https://cdn.example.com/clip.mp4",
        "cover_url": "https://cdn.example.com/cover.jpg",
        "keyframes": [
            "https://cdn.example.com/kf-1.jpg",
            "https://cdn.example.com/kf-2.jpg",
        ],
    }
    frames = extract_keyframes(post, {"url": post["video_url"]})
    assert "https://cdn.example.com/kf-1.jpg" in frames
    assert "https://cdn.example.com/cover.jpg" in frames


def test_media_items_from_post_include_phash_and_keyframes() -> None:
    post = {
        "id": "p1",
        "platform": "weibo",
        "content": "配图文字「核实中」",
        "image_url": "https://cdn.example.com/a.jpg?token=abc",
        "luminance_grid": _gradient_grid(),
        "video_url": "https://cdn.example.com/a.mp4",
        "cover_url": "https://cdn.example.com/cover.jpg",
    }
    items = media_items_from_post(post)
    types = {item["media_type"] for item in items}
    assert "image" in types
    assert "video" in types
    image = next(item for item in items if item["media_type"] == "image")
    assert image["phash"] == average_hash(_gradient_grid())
    assert image["ocr_text"]
    video = next(item for item in items if item["media_type"] == "video")
    assert video["keyframe_urls"]
