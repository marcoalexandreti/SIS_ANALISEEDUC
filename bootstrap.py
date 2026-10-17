# -*- coding: utf-8 -*-
"""Inicializador e diagnóstico para Windows 11, sem dependências externas."""
import argparse
import datetime
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
LOG_FILE = LOGS / "ultima_execucao.log"
REQUIRED = ("PySide6", "reportlab", "xlsxwriter")

class Logger:
    def __init__(self):
        LOGS.mkdir(exist_ok=True)
        self.f = LOG_FILE.open("w", encoding="utf-8")
        self.line("FLUXO ESCOLAR - DIAGNOSTICO DE INICIALIZACAO")
        self.line(f"Data: {datetime.datetime.now().isoformat(timespec='seconds')}")
        self.line(f"Pasta: {ROOT}")
    def line(self, line):
        text = str(line)
        print(text, flush=True)
        self.f.write(text + "\n")
        self.f.flush()
    def run(self, args, cwd=None):
        cmd=[str(p) for p in args]
        self.line("Executando: " + " ".join('"'+p+'"' if ' ' in p else p for p in cmd))
        try:
            # The same output goes both to the user and the diagnostic file.
            proc = subprocess.Popen(cmd, cwd=cwd or ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, universal_newlines=True,
                                    errors="replace", bufsize=1)
            for line in proc.stdout:
                self.line(line.rstrip("\r\n"))
            proc.wait()
            self.line(f"Codigo de saida: {proc.returncode}")
            return proc.returncode
        except Exception as exc:
            self.line("ERRO AO EXECUTAR: " + repr(exc))
            return 127
    def close(self):self.f.close()

PROBE=(
    "import sys, struct, importlib; "
    "print('Python', sys.version.split()[0], 'arquitetura', struct.calcsize('P')*8, 'bits'); "
    "mods=('PySide6','reportlab','xlsxwriter'); "
    "[(importlib.import_module(m),print(m,'OK')) for m in mods]"
)


def probe(python, log):
    return log.run([python, '-c', PROBE]) == 0

def python_info(python,log):
    return log.run([python,'-c',
                    'import sys, struct; print(sys.executable, sys.version.split()[0], struct.calcsize("P")*8)'])==0

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--diagnose',action='store_true')
    parser.add_argument('--reparar',action='store_true')
    args=parser.parse_args()
    log=Logger()
    try:
        python=sys.executable
        log.line(f"Python inicializador: {python}")
        if not python_info(python,log):
            log.line('FALHA: Python do inicializador indisponivel.');return 1
        if sys.version_info < (3, 11):
            log.line('FALHA: Instale Python 3.11 ou superior (64 bits).');return 1
        if sys.maxsize <= 2**32:
            log.line('FALHA: Python de 32 bits. Instale uma edicao de 64 bits.');return 1
        if not (ROOT/'gui.py').exists() or not (ROOT/'schema.sql').exists():
            log.line('FALHA: faltam arquivos do sistema. Extraia TODO o ZIP antes de executar.');return 1
        env_python=ROOT/'.venv'/'Scripts'/'python.exe' if os.name == 'nt' else ROOT/'.venv'/'bin'/'python'
        log.line(f"Ambiente virtual: {env_python}")
        if args.reparar and env_python.exists():
            target=ROOT/('.venv_antigo_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
            log.line('Renomeando ambiente antigo para '+str(target))
            (ROOT/'.venv').rename(target)
        env_ok=env_python.is_file() and probe(str(env_python),log)
        sys_ok=probe(python,log)
        if args.diagnose:
            log.line('Diagnostico concluido. Dados mantidos, nenhuma instalacao realizada.')
            log.line('Para iniciar, execute INICIAR.bat.');return 0 if (env_ok or sys_ok) else 2
        if env_ok:
            chosen=str(env_python)
            log.line('Usando ambiente virtual existente e completo.')
        elif sys_ok:
            chosen=python
            log.line('Usando Python instalado no Windows com bibliotecas ja disponiveis.')
        else:
            if not env_python.is_file():
                log.line('Criando ambiente virtual Python...')
                if log.run([python,'-m','venv',str(ROOT/'.venv')]) !=0:
                    log.line('Falha ao criar ambiente. Verifique permissoes da pasta, instalacao do Python e antivirus.')
                    return 3
            if not env_python.is_file():
                log.line('FALHA: python.exe do ambiente virtual nao foi criado.');return 3
            log.line('Instalando dependencias necessarias. E necessario acesso a internet na primeira vez.')
            # Running pip in the venv avoids unexpected package mixing with the user's Python.
            result=log.run([str(env_python),'-m','pip','install','--disable-pip-version-check',
                           '--prefer-binary','-r',str(ROOT/'requirements.txt')])
            if result!=0 or not probe(str(env_python),log):
                log.line('FALHA: dependencias nao puderam ser instaladas. Reveja conexao com a internet,')
                log.line('antivirus/proxy e versao do Python. Consulte logs\\ultima_execucao.log.')
                return 4
            chosen=str(env_python)
        from inicializar_banco import inicializar
        banco = inicializar(ROOT/'fluxo_escolar.sqlite')
        log.line('Banco local preparado: '+str(banco))
        log.line('Iniciando interface grafica...')
        result=log.run([chosen,'-u',str(ROOT/'gui.py')])
        if result:
            log.line('FALHA NA INTERFACE: codigo '+str(result))
            log.line('Encaminhe o arquivo logs\\ultima_execucao.log para diagnostico.')
            return result
        log.line('Aplicacao encerrada normalmente.')
        return 0
    except Exception:
        log.line(traceback.format_exc())
        return 1
    finally:
        log.close()

if __name__=='__main__':sys.exit(main())
