"""The Brain's image pipeline stages only requested frames; no training is run."""
import numpy as np
import pytest

from nnslr_tools import brain_train, road_type_data, road_type_signs


def test_picture_source_reads_only_requested_segments_and_preserves_frame_order(tmp_path):
    route = 'r1'
    shape = (2, road_type_data.FRAME_HEIGHT, road_type_data.FRAME_WIDTH, 3)
    for segment, values in ((0, (13, 31)), (1, (71, 17))):
        path = road_type_signs.picture_path(tmp_path, route, segment)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, np.broadcast_to(np.array(values, dtype=np.uint8)[:, None, None, None], shape))
    frames = [
        {'segment': 0, 'picture': 0, 'present': True},
        {'segment': 0, 'picture': 1, 'present': False},
        {'segment': 1, 'picture': 1, 'present': True},
        {'segment': 2, 'picture': 0, 'present': False},
    ]
    source = brain_train._pictures(tmp_path, [{'route': route, 'rate': brain_train.RATE_HZ, 'frames': frames}],
                                   dry_run=False, log=lambda _: None)[route]
    assert source._pixels is None
    assert source[[2, 1, 0, 2, 3]][:, 0, 0, 0].tolist() == [17, 0, 13, 17, 0]
    assert source[[2]][:, 0, 0, 0].tolist() == [17]
    last = source._pixels
    assert isinstance(last, np.memmap)
    source[[2]]
    assert source._pixels is last


def test_store_never_materializes_whole_routes_and_matches_image_layout():
    torch = pytest.importorskip('torch')
    source = np.arange(6 * 2 * 3 * 3, dtype=np.uint8).reshape(6, 2, 3, 3)

    class LargeRoute:
        def __len__(self):
            return 400_000

        def __array__(self, *args, **kwargs):
            raise AssertionError('the whole route is too large to load')

        def __getitem__(self, positions):
            assert len(positions) == 2
            return source[positions]

    store = brain_train.BrainFrameStore({'a': LargeRoute(), 'b': source}, 'cpu')
    order = [('a', [5, 3]), ('b', [0, 5])]
    batch = store.batch(order)
    expected = torch.from_numpy(np.stack([source[positions] for _, positions in order])).permute(0, 1, 4, 2, 3).float() / 255
    assert batch.dtype == torch.float32
    torch.testing.assert_close(batch, expected, rtol=0, atol=0)
