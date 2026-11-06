"""T10: o README aponta trechos que existem (v1-R11)."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
README = (RAIZ / "README.md").read_text(encoding="utf-8")
REFERENCIA = re.compile(r"`(aurora/[a-z_]+\.py):(\d+)` \(`([^`]+)`\)")


def test_readme_tem_as_tres_secoes():
    titulos = re.findall(r"^## (.+)$", README, re.MULTILINE)
    assert titulos == ["Arquitetura", "Garantias", "Como rodar"]


def test_referencias_do_readme_apontam_para_o_simbolo_na_linha():
    referencias = REFERENCIA.findall(README)
    assert len(referencias) >= 5
    for arquivo, linha, simbolo in referencias:
        linhas = (RAIZ / arquivo).read_text(encoding="utf-8").splitlines()
        n = int(linha)
        assert 1 <= n <= len(linhas), f"{arquivo}:{linha} fora do arquivo"
        assert simbolo in linhas[n - 1], f"{arquivo}:{linha} não tem `{simbolo}`"


def test_readme_traz_os_comandos_de_subida_e_restauracao():
    assert "uv run uvicorn aurora.api:app --port 8000" in README
    assert "uv run python -m aurora.restaurar" in README


def test_env_example_so_tem_nomes():
    linhas = (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()
    atribuicoes = [l for l in linhas if l and not l.startswith("#")]
    assert "GOOGLE_API_KEY=" in atribuicoes
    assert all(re.fullmatch(r"[A-Z_]+=", l) for l in atribuicoes)
