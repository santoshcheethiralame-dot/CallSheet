import json

from callsheet.domain import Review, Shot, load_review, load_shots


def test_load_shots_reads_the_phase_1_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([
        {"shot": "SH001", "scene": "scenes/SH001.blend", "samples": 16, "frames": [1, 2, 3]},
    ]), encoding="utf-8")

    shots = load_shots(str(path))

    assert len(shots) == 1
    assert shots[0].id == "SH001"
    assert shots[0].frames == [1, 2, 3]
    assert shots[0].samples == 16


def test_shots_default_to_final_quality_not_cut_and_normal_priority(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([
        {"shot": "SH001", "scene": "a.blend", "samples": 16, "frames": [1]},
    ]), encoding="utf-8")

    shot = load_shots(str(path))[0]

    assert shot.quality == "final"
    assert shot.is_cut is False
    assert shot.priority == 50


def test_manifest_may_override_production_fields(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([
        {"shot": "SH002", "scene": "b.blend", "samples": 64, "frames": [1],
         "priority": 90, "is_cut": True, "quality": "proxy"},
    ]), encoding="utf-8")

    shot = load_shots(str(path))[0]

    assert shot.priority == 90
    assert shot.is_cut is True
    assert shot.quality == "proxy"


def test_load_review_reads_deadline_and_required_shots(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(json.dumps({
        "name": "Director review",
        "deadline_epoch_s": 1786050000,
        "required_shots": ["SH001", "SH003"],
    }), encoding="utf-8")

    review = load_review(str(path))

    assert review.name == "Director review"
    assert review.deadline_epoch_s == 1786050000
    assert review.required_shots == ["SH001", "SH003"]
