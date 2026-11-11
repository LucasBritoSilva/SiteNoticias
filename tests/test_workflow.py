"""Confere o essencial do workflow lendo o texto (sem PyYAML, que não é dependência do projeto).

A execução real depende do GitHub Actions e não é coberta aqui.
"""
from pathlib import Path

TEXTO = (Path(__file__).parent.parent / ".github" / "workflows" / "atualizar-site.yml").read_text(encoding="utf-8")


def test_horarios_em_utc():
    assert 'cron: "50 10 * * *"' in TEXTO  # 06:50 em Cuiabá (UTC-4)
    assert 'cron: "50 16 * * *"' in TEXTO  # 12:50 em Cuiabá
    assert 'cron: "50 21 * * *"' in TEXTO  # 17:50 em Cuiabá
    assert 'cron: "30 2 * * *"' in TEXTO   # 22:30 em Cuiabá = 02:30 UTC do dia seguinte
    assert TEXTO.count("- cron:") == 4


def test_gatilho_manual_e_concorrencia():
    assert "workflow_dispatch:" in TEXTO
    assert "concurrency:" in TEXTO and "cancel-in-progress: false" in TEXTO


def test_segredo_vem_de_secrets_e_nao_esta_no_arquivo():
    assert "${{ secrets.GEMINI_API_KEY }}" in TEXTO
    assert "AIza" not in TEXTO


def test_push_so_depois_de_gerar_e_com_nova_tentativa():
    assert TEXTO.index("python scripts/main.py") < TEXTO.index("git push")
    assert "git pull --rebase" in TEXTO and "--force" not in TEXTO


def test_workflow_versiona_o_cache_dos_resumos():
    assert "git add -A dados" in TEXTO
