"""tools/drone_mesh — 격자 묶기와 뒤집기."""
import numpy as np

from tools.drone_mesh import cluster_decimate, to_body


def test_cluster_merges_close_vertices_and_drops_collapsed_faces():
    v = np.array([[0, 0, 0], [0.1, 0, 0], [5, 0, 0], [0, 5, 0], [5, 5, 0]], float)
    f = np.array([[0, 1, 2],       # 0 · 1 이 한 칸 → 찌그러져 버린다
                  [0, 2, 3], [1, 2, 3],   # 같은 면이 된다 → 하나만
                  [2, 4, 3]])
    nv, nf = cluster_decimate(v, f, cell=1.0)
    assert len(nv) == 4 and len(nf) == 2
    assert np.allclose(nv[nf[0, 0]], [0.05, 0, 0])      # 묶인 정점은 평균


def test_to_body_flips_upside_down_and_converts_mm():
    v = np.array([[0, 100, 50], [200, -100, -30]], float)   # 원본: 다리(+z)가 위에 있다
    b = to_body(v)
    assert np.allclose(b, [[-0.1, -0.1, -0.04], [0.1, 0.1, 0.04]])   # z 가 뒤집히고 중심이 원점
