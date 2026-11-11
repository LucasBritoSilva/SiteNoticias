"""Garante que nenhum segredo foi gravado no repositório."""
import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).parent.parent
PADROES = [
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
]


def arquivos_versionados():
    saida = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8")
    nomes = saida.stdout.split("\n") if saida.returncode == 0 else []
    return [RAIZ / n for n in nomes if n and Path(RAIZ / n).suffix not in {".png", ".jpg", ".webp", ".gif"}]


def test_nenhuma_chave_ou_token_no_codigo():
    achados = []
    for arq in arquivos_versionados():
        if not arq.exists():
            continue
        texto = arq.read_text(encoding="utf-8", errors="ignore")
        achados += [f"{arq.name}: {p.pattern}" for p in PADROES if p.search(texto)]
    assert not achados, achados
