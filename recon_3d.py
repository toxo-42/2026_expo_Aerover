# recon_3d.py

import os


def run_reconstruct(src_dir, out_dir):
    """src_dir의 이미지들로 3D 복원 → out_dir에 model.glb 생성"""
    os.makedirs(out_dir, exist_ok=True)
    print("[3D] 복원 미구현")
    return None


def load_model(out_dir):
    """생성된 glb 경로 반환, 없으면 None"""
    p = os.path.join(out_dir, "model.glb")
    return p if os.path.exists(p) else None


if __name__ == "__main__":
    run_reconstruct("received", "recon3d")