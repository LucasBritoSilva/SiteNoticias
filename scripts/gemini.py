"""Cliente mínimo da API REST do Gemini (apenas requests)."""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
MODELO_PADRAO = "gemini-flash-latest"
STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}


class GeminiErro(RuntimeError):
    """Falha de comunicação com a API (rede, cota, chave inválida...)."""


class RespostaInvalida(ValueError):
    """A resposta chegou, mas não é o JSON esperado."""


class GeminiClient:
    def __init__(
        self,
        chave: str | None = None,
        modelo: str | None = None,
        sessao: requests.Session | None = None,
        intervalo_minimo: float = 4.0,
        esperas: tuple[float, ...] = (5.0, 20.0),
        timeout: int = 120,
    ):
        self.chave = chave or os.environ.get("GEMINI_API_KEY", "")
        if not self.chave:
            raise GeminiErro("Variável de ambiente GEMINI_API_KEY não definida.")
        self.modelo = modelo or os.environ.get("GEMINI_MODEL") or MODELO_PADRAO
        self.http = sessao or requests.Session()
        self.intervalo_minimo = intervalo_minimo
        self.esperas = esperas
        self.timeout = timeout
        self._ultima = 0.0

    def gerar(self, prompt: str) -> str:
        """Envia o prompt e devolve o texto da resposta (esperado: JSON)."""
        corpo = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
                "maxOutputTokens": 8192,
            },
        }
        cabecalhos = {"x-goog-api-key": self.chave, "Content-Type": "application/json"}
        url = API_URL.format(modelo=self.modelo)
        tentativas = len(self.esperas) + 1
        for i in range(tentativas):
            self._respeitar_intervalo()
            try:
                resp = self.http.post(url, headers=cabecalhos, json=corpo, timeout=self.timeout)
            except requests.RequestException as exc:
                erro = f"erro de rede: {exc}"
            else:
                self._ultima = time.monotonic()
                if resp.status_code == 200:
                    return extrair_texto(resp.json())
                erro = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in STATUS_TRANSITORIOS:
                    raise GeminiErro(f"Gemini recusou a requisição ({erro})")
            if i < tentativas - 1:
                log.warning("Gemini: %s; nova tentativa em %.0fs", erro, self.esperas[i])
                time.sleep(self.esperas[i])
        raise GeminiErro(f"Gemini indisponível após {tentativas} tentativas ({erro})")

    def _respeitar_intervalo(self) -> None:
        falta = self.intervalo_minimo - (time.monotonic() - self._ultima)
        if self._ultima and falta > 0:
            time.sleep(falta)


def extrair_texto(resposta: dict) -> str:
    """Pega o texto do primeiro candidato; levanta RespostaInvalida se não houver."""
    try:
        partes = resposta["candidates"][0]["content"]["parts"]
        texto = "".join(p.get("text", "") for p in partes)
    except (KeyError, IndexError, TypeError) as exc:
        motivo = resposta.get("promptFeedback") if isinstance(resposta, dict) else None
        raise RespostaInvalida(f"Resposta sem texto (feedback: {motivo})") from exc
    if not texto.strip():
        raise RespostaInvalida("Resposta vazia")
    return texto


def interpretar_json(texto: str):
    """Converte o texto em JSON, tolerando cercas ```json ... ```."""
    limpo = texto.strip()
    cerca = re.match(r"^```(?:json)?\s*(.*?)\s*```$", limpo, re.DOTALL)
    if cerca:
        limpo = cerca.group(1)
    try:
        return json.loads(limpo)
    except json.JSONDecodeError as exc:
        raise RespostaInvalida(f"JSON inválido: {exc}") from exc
