import json

import numpy as np
import pytest

from nnslr_tools import reader_country as country
from nnslr_tools.car_release import manifest_of
from nnslr_tools.exporting import reader_io_contract


def test_frame_country_is_a_per_frame_gps_join(tmp_path):
    route = "0000001b--a0e065f57f"
    path = tmp_path / "derived" / "gps" / route / "positions.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps({"frame_key": f"0/{second}", "segment_index": 0,
                                          "output_index": second, "country": code})
                              for second, code in ((1, "DE"), (2, "CH"), (4, None))))
    frame = {"route_id": route, "segment_index": 0, "output_index": 1, "decoded_frame_index": 20}
    cache = {}
    assert country.frame_country(tmp_path, cache, frame) == "DE"
    assert country.frame_country(tmp_path, cache, {**frame, "output_index": 2}) == "CH"
    assert country.frame_country(tmp_path, cache, {**frame, "output_index": 3}) == "unknown"
    assert country.video_countries(tmp_path, route)[0][:6] == ("unknown", "DE", "CH", "CH", "unknown", "unknown")
    assert country.video_countries(tmp_path, "missing") == {}


def test_contract_keeps_legacy_input_and_names_the_new_one():
    image_only = {"classes": ["stop", "yield"], "input_size": 160}
    previous = reader_io_contract(image_only)
    assert country.contract_codes(previous) == ()
    current = reader_io_contract({**image_only, "country_codes": ["unknown", "DE", "CH"]})
    assert current["input"] == previous["input"]
    assert country.contract_codes(current) == ("unknown", "DE", "CH")
    with pytest.raises(ValueError, match="invalid classifier"):
        country.contract_codes({**current, "country_input": {**current["country_input"], "shape": [1, 2]}})
    assert set(manifest_of("new", classifier_country=True)["settings"]) == {"classifier_country_policy"}
    assert set(manifest_of("new", brain=True, classifier_country=True)["settings"]) == {"country_policy"}
    assert "settings" not in manifest_of("legacy")


def test_numpy_runner_uses_unknown_for_missing_and_unseen_without_filtering():
    tensor = np.zeros((1, 3, 160, 160), dtype=np.float32)
    given = []

    def classify(**inputs):
        assert inputs["images"] is tensor
        given.append(inputs["country"].tolist())
        return {"logits": np.array([[2, 3, 4]])}

    codes = ("unknown", "CH", "DE")
    for value in ("de", "IT", None):
        assert country.network_for_country(classify, codes, value)(tensor).tolist() == [[2, 3, 4]]
    assert given == [[[0, 0, 1]], [[1, 0, 0]], [[1, 0, 0]]]
    assert country.network_for_country(classify, (), "DE") is classify


def test_country_weights_can_start_from_legacy_or_a_reordered_vocabulary():
    torch = pytest.importorskip("torch")
    from torchvision.models import mobilenet_v3_small

    from nnslr_tools.training import _load_start

    labels = ["not_a_sign", "stop"]
    old = country.build_reader(torch, mobilenet_v3_small, None, len(labels), ())
    new = country.build_reader(torch, mobilenet_v3_small, None, len(labels), ("unknown", "CH", "DE"))
    payload = {"task": "reader", "architecture": "mobilenet_v3_small", "class_scheme": "v2", "input_size": 160,
               "classes": labels, "state_dict": old.state_dict()}
    _load_start(torch, new, payload, labels, 160, "classifier", "old")
    assert torch.equal(old.classifier[3].weight, new.classifier[3].weight)
    assert torch.count_nonzero(new.country_projection.weight) == 0
    with torch.no_grad():
        new.country_projection.weight[:, 1] = 3
        new.country_projection.weight[:, 2] = 5
    transferred = country.build_reader(torch, mobilenet_v3_small, None, len(labels), ("unknown", "DE", "US", "CH"))
    _load_start(torch, transferred, {**payload, "country_codes": list(new.country_codes), "state_dict": new.state_dict()},
                labels, 160, "classifier", "new")
    assert transferred.country_projection.weight[0].tolist() == [0, 5, 0, 3]
