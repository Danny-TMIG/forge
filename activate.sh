cd "$(dirname "${BASH_SOURCE[0]}")"
source .venv/bin/activate
export FORGE_MLX_MODEL="${FORGE_MLX_MODEL:-mlx-community/Qwen2.5-Coder-7B-Instruct-4bit}"
echo "forge ready — forge hubs | forge ask | forge prove | forge train"
