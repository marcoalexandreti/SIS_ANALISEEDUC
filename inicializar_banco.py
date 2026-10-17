"""Cria somente a estrutura local; nunca importa dados ou apaga registros."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def inicializar(destino=None):
    destino = Path(destino) if destino is not None else ROOT / 'fluxo_escolar.sqlite'
    destino.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destino) as conn:
        conn.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
    return destino

if __name__ == '__main__':
    print('Banco local inicializado:', inicializar())
