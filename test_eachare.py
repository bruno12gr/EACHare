import os
import shutil
import subprocess
import time
import json


peer1_port = 8001
peer2_port = 8002
peer1_dir = "compartilhados1"
peer2_dir = "compartilhados2"
peer1_neighbors = "vizinhos1.txt"
peer2_neighbors = "vizinhos2.txt"
arquivo_peer1 = os.path.join(peer1_dir, "teste1.txt")
arquivo_peer2 = os.path.join(peer2_dir, "teste2.txt")
arquivo_baixado = os.path.join(peer1_dir, "teste2.txt")
stats_file = "peer_stats.json"  # Arquivo para exportar estatísticas

# Limpeza anterior
for pasta in [peer1_dir, peer2_dir]:
    if os.path.exists(pasta):
        shutil.rmtree(pasta)
os.makedirs(peer1_dir)
os.makedirs(peer2_dir)

# Criando arquivos
conteudo_peer1 = "Conteúdo do peer 1"
conteudo_peer2 = "Conteúdo do peer 2" * 100  # Conteúdo maior para forçar múltiplos chunks

with open(arquivo_peer1, "w") as f:
    f.write(conteudo_peer1)

with open(arquivo_peer2, "w") as f:
    f.write(conteudo_peer2)

# Criando arquivos de vizinhança
with open(peer1_neighbors, "w") as f:
    f.write(f"127.0.0.1:{peer2_port}")

with open(peer2_neighbors, "w") as f:
    f.write(f"127.0.0.1:{peer1_port}")

# Inicia os peers
peer1 = subprocess.Popen(
    ["python", "eachare.py", f"127.0.0.1:{peer1_port}", peer1_neighbors, peer1_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1  # Buffer de linha
)

peer2 = subprocess.Popen(
    ["python", "eachare.py", f"127.0.0.1:{peer2_port}", peer2_neighbors, peer2_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1  # Buffer de linha
)

# Aguarda inicialização
time.sleep(2)

# Simula comandos no peer1 para obter peers, enviar HELLO, buscar e baixar
comandos = [
    "2\n",  # obter peers
    "1\n",  # listar peers
    "1\n",  # enviar HELLO (para peer2)
    "4\n",  # buscar arquivos
    "1\n",  # selecionar primeiro arquivo para download
    "5\n",  # exibir estatísticas (após download)
    "9\n"  # sair
]

# Envia comandos para o peer1
for cmd in comandos:
    peer1.stdin.write(cmd)
    peer1.stdin.flush()
    time.sleep(1.5)  # Tempo para processar cada comando

# Aguarda processamento final
time.sleep(3)

# Captura saída do peer1 para análise
peer1_output, _ = peer1.communicate()

# Verifica se o arquivo foi baixado
if os.path.exists(arquivo_baixado):
    with open(arquivo_baixado, "r") as f:
        conteudo_baixado = f.read()
        print(f"[SUCESSO] Arquivo baixado com conteúdo (tamanho: {len(conteudo_baixado)} bytes)")

        # Verifica integridade do conteúdo
        if conteudo_baixado == conteudo_peer2:
            print("[SUCESSO] Conteúdo do arquivo está correto")
        else:
            print(
                f"[ERRO] Conteúdo do arquivo incorreto. Esperado: {len(conteudo_peer2)} bytes, Recebido: {len(conteudo_baixado)} bytes")
else:
    print("[ERRO] Arquivo não foi baixado.")

# Validação das estatísticas de chunk
print("\nValidando estatísticas de chunk...")

# 1. Verifica downloads iniciados e concluídos
if "downloads_iniciados: 1" in peer1_output:
    print("[SUCESSO] 1 download iniciado registrado")
else:
    print("[ERRO] download iniciado não registrado")

if "downloads_concluidos: 1" in peer1_output:
    print("[SUCESSO] 1 download concluído registrado")
else:
    print("[ERRO] download concluído não registrado")

# 2. Verifica bytes baixados
expected_bytes = len(conteudo_peer2)
if f"bytes_baixados: {expected_bytes}" in peer1_output:
    print(f"[SUCESSO] {expected_bytes} bytes baixados registrados")
else:
    print(f"[ERRO] bytes baixados incorretos. Esperado: {expected_bytes}")

# 3. Verifica estatísticas de tempo de download
# Procura por padrão: "Tam. chunk | N peers | Tam. arquivo | N | Tempo [s] | Desvio"
stats_lines = [line for line in peer1_output.splitlines() if "|" in line and "Tam. chunk" not in line]

if stats_lines:
    print("[SUCESSO] Estatísticas de tempo encontradas:")
    for line in stats_lines:
        print(f"  {line}")

    # Extrai valores para validação adicional
    parts = stats_lines[0].split('|')
    chunk_size = int(parts[0].strip())
    num_peers = int(parts[1].strip())
    file_size = int(parts[2].strip())
    num_samples = int(parts[3].strip())
    tempo_medio = float(parts[4].strip())

    # Valida valores
    if chunk_size == 256:
        print("[SUCESSO] Tamanho de chunk correto (256 bytes)")
    else:
        print(f"[ERRO] Tamanho de chunk incorreto: {chunk_size}")

    if num_peers == 1:
        print("[SUCESSO] Número de peers correto (1)")
    else:
        print(f"[ERRO] Número de peers incorreto: {num_peers}")

    if file_size == expected_bytes:
        print(f"[SUCESSO] Tamanho de arquivo correto ({expected_bytes} bytes)")
    else:
        print(f"[ERRO] Tamanho de arquivo incorreto: {file_size}")

    if num_samples == 1:
        print("[SUCESSO] 1 amostra registrada")
    else:
        print(f"[ERRO] Número de amostras incorreto: {num_samples}")

    if tempo_medio > 0:
        print(f"[SUCESSO] Tempo médio válido: {tempo_medio:.5f}s")
    else:
        print("[ERRO] Tempo médio inválido")
else:
    print("[ERRO] Nenhuma estatística de tempo encontrada na saída")

# Finaliza subprocessos
peer1.kill()
peer2.kill()

# Salva estatísticas para análise posterior
with open(stats_file, "w") as f:
    stats_data = {
        "output": peer1_output,
        "expected_bytes": expected_bytes,
        "actual_bytes": len(conteudo_baixado) if os.path.exists(arquivo_baixado) else 0,
        "file_correct": conteudo_baixado == conteudo_peer2 if os.path.exists(arquivo_baixado) else False
    }
    json.dump(stats_data, f, indent=2)

print(f"\nRelatório completo salvo em: {stats_file}")