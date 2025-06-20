import os
import socket
import threading
import time
import base64
import math
from collections import defaultdict


class EacharePeer:
    def __init__(self, address, port, neighbors_file, shared_dir):
        self.address = address
        self.port = port
        self.full_address = f"{address}:{port}"
        self.peers = {}
        self.clock = 0
        self.clock_lock = threading.Lock()
        self.shared_dir = shared_dir
        self.running = True
        self.neighbors_file = neighbors_file
        self.arquivos_recebidos = {}
        self.chunk_size = 256  # Valor enunciado

        # Estatísticas de transferência
        self.estatisticas_transferencia = {
            'chunks_enviados': 0,
            'chunks_recebidos': 0,
            'bytes_enviados': 0,
            'bytes_recebidos': 0
        }

        # Estatísticas de desempenho de download
        self.estatisticas_download = defaultdict(list)  # {(chunk_size, num_peers, file_size): [tempos]}

        with open(neighbors_file, 'r') as f:
            for line in f:
                peer = line.strip()
                if peer and peer != self.full_address:
                    self.peers[peer] = {"status": "OFFLINE", "clock": 0}
                    print(f"Adicionando novo peer {peer} status OFFLINE")

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((address, port))
        self.server_socket.listen(5)

        # Var para download em andamento
        self.download_em_andamento = None
        self.download_lock = threading.Lock()

    def increment_clock(self):
        with self.clock_lock:
            self.clock += 1
            print(f"=> Atualizando relogio para {self.clock}")

    def update_peer_status(self, peer, status, pClock):
        current = self.peers.get(peer)
        if current:
            if pClock > current["clock"]:
                if current["status"] != status:
                    print(f"Atualizando peer {peer} status {status} (clock {pClock})")
                self.peers[peer] = {"status": status, "clock": pClock}
        else:
            print(f"Adicionando novo peer {peer} status {status} (clock {pClock})")
            self.peers[peer] = {"status": status, "clock": pClock}
            self.add_peer_to_neighbors_file(peer)

    def send_message(self, destination, message):
        try:
            addr, port = destination.split(':')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((addr, int(port)))
                s.sendall(message.encode())
                print(f"Encaminhando mensagem \"{message}\" para {destination}")

                # Atualizar estatísticas de transferência
                with self.clock_lock:
                    self.estatisticas_transferencia['bytes_enviados'] += len(message)

                self.update_peer_status(destination, "ONLINE", self.clock)
        except Exception as e:
            print(f"Erro ao conectar em {destination}: {str(e)}")
            self.update_peer_status(destination, "OFFLINE", self.clock)

    def create_peer_list_response(self, exclude_peer):
        peer_list = []
        for peer, info in self.peers.items():
            if peer != exclude_peer:
                peer_list.append(f"{peer}:{info['status']}:{info['clock']}")
        return f"{self.full_address} {self.clock} PEER_LIST {len(peer_list)} {' '.join(peer_list)}"

    def process_peer_list(self, peers_data):
        count = int(peers_data[0])
        for i in range(1, count + 1):
            peer_info = peers_data[i].split(':')
            peer_addr = f"{peer_info[0]}:{peer_info[1]}"
            status = peer_info[2]
            clock = int(peer_info[3])
            self.add_peer(peer_addr)
            self.update_peer_status(peer_addr, status, clock)

    def is_peer_in_file(self, peer_address):
        return peer_address in self.peers

    def add_peer(self, peer_address):
        if not self.is_peer_in_file(peer_address):
            self.add_peer_to_neighbors_file(peer_address)

    def add_peer_to_neighbors_file(self, peer_address):
        with open(self.neighbors_file, 'a') as file:
            file.write(f"\n{peer_address}")

    def buscar_arquivos(self):
        self.arquivos_recebidos = {}
        self.increment_clock()
        message = f"{self.full_address} {self.clock} LS"

        # Enviar pedido LS para todos os peers online
        for peer, info in self.peers.items():
            if info["status"] == "ONLINE":
                self.send_message(peer, message)

        time.sleep(2)

        # Agrupar arquivos por (nome, tamanho)
        arquivos_agrupados = {}
        for peer, arquivos in self.arquivos_recebidos.items():
            for nome, tamanho in arquivos:
                chave = (nome, tamanho)
                if chave not in arquivos_agrupados:
                    arquivos_agrupados[chave] = []
                arquivos_agrupados[chave].append(peer)

        # Converter para lista ordenada
        lista_arquivos = []
        for (nome, tamanho), peers in arquivos_agrupados.items():
            lista_arquivos.append((nome, tamanho, peers))

        # Exibir resultados
        if not lista_arquivos:
            print("Nenhum arquivo encontrado.")
            return

        print("\nArquivos encontrados na rede:")
        print("     Nome        | Tamanho  | Peers")
        for i, (nome, tamanho, peers) in enumerate(lista_arquivos, 1):
            peers_str = ', '.join(peers)
            print(f"[{i}]  {nome}  | {tamanho}      | {peers_str}")

        print("[0] Cancelar")
        escolha = int(input("> "))
        if escolha == 0:
            return

        # Obter arquivo selecionado
        arquivo_selecionado = lista_arquivos[escolha - 1]
        nome_arquivo = arquivo_selecionado[0]
        tamanho_arquivo = arquivo_selecionado[1]
        peers_com_arquivo = arquivo_selecionado[2]

        print(f"Você selecionou: {nome_arquivo} (tamanho: {tamanho_arquivo})")

        # Iniciar download fragmentado
        self.iniciar_download_fragmentado(nome_arquivo, tamanho_arquivo, peers_com_arquivo)

    def iniciar_download_fragmentado(self, nome_arquivo, tamanho_arquivo, peers):
        # Calcular número de chunks
        num_chunks = math.ceil(tamanho_arquivo / self.chunk_size)

        # Inicializar estrutura de download
        with self.download_lock:
            self.download_em_andamento = {
                'nome_arquivo': nome_arquivo,
                'tamanho': tamanho_arquivo,
                'num_chunks': num_chunks,
                'chunks_recebidos': 0,
                'chunks': [None] * num_chunks,
                'start_time': time.time(),
                'peers': peers,  # Armazenar lista de peers para estatísticas
                'chunk_size': self.chunk_size  # Armazenar tamanho de chunk usado
            }

        # Solicitar cada chunk
        for chunk_index in range(num_chunks):
            # Escolher peer (round-robin simples)
            peer_index = chunk_index % len(peers)
            peer_selecionado = peers[peer_index]

            # Calcular tamanho real do chunk (último pode ser menor)
            tamanho_chunk = self.chunk_size
            if chunk_index == num_chunks - 1:
                tamanho_chunk = tamanho_arquivo - (chunk_index * self.chunk_size)

            self.increment_clock()
            msg = f"{self.full_address} {self.clock} DL {nome_arquivo} {self.chunk_size} {chunk_index}"
            self.send_message(peer_selecionado, msg)
            print(f"Solicitando chunk {chunk_index} de {nome_arquivo} para {peer_selecionado}")

    def handle_message(self, conn, addr):
        data = conn.recv(1024 * 100).decode()
        if not data:
            return

        # Atualizar estatísticas de transferência
        with self.clock_lock:
            self.estatisticas_transferencia['bytes_recebidos'] += len(data)

        # Mostrar apenas parte da mensagem para não poluir a saída
        preview = data[:60] + "..." if len(data) > 60 else data
        print(f"Resposta recebida: \"{preview}\"")

        parts = data.strip().split()
        origin = parts[0]
        msg_clock = int(parts[1])
        msg_type = parts[2]

        with self.clock_lock:
            self.clock = max(msg_clock, self.clock)
        self.increment_clock()

        if msg_type == "HELLO":
            self.update_peer_status(origin, "ONLINE", msg_clock)
        elif msg_type == "GET_PEERS":
            response = self.create_peer_list_response(origin)
            self.send_message(origin, response)
        elif msg_type == "PEER_LIST":
            self.process_peer_list(parts[3:])
        elif msg_type == "BYE":
            self.update_peer_status(origin, "OFFLINE", msg_clock)
        elif msg_type == "LS":
            response = self.create_ls_list_response()
            self.send_message(origin, response)
        elif msg_type == "LS_LIST":
            self.process_ls_list(origin, parts[3:])
        elif msg_type == "DL":
            filename = parts[3]
            chunk_size = int(parts[4])
            chunk_index = int(parts[5])
            self.enviar_chunk_arquivo(origin, filename, chunk_size, chunk_index)
        elif msg_type == "FILE":
            filename = parts[3]
            chunk_size = int(parts[4])
            chunk_index = int(parts[5])
            encoded_data = ' '.join(parts[6:])  # Juntar todos os tokens restantes
            self.processar_chunk_recebido(filename, chunk_index, encoded_data)

        conn.close()

    def enviar_chunk_arquivo(self, destination, filename, chunk_size, chunk_index):
        caminho = os.path.join(self.shared_dir, filename)
        try:
            with open(caminho, 'rb') as f:
                # Posicionar no início do chunk
                f.seek(chunk_index * chunk_size)

                # Ler o chunk (último pode ser menor)
                data = f.read(chunk_size)
                encoded = base64.b64encode(data).decode()

                self.increment_clock()
                msg = f"{self.full_address} {self.clock} FILE {filename} {chunk_size} {chunk_index} {encoded}"
                self.send_message(destination, msg)

                # Atualizar estatísticas de transferência
                with self.clock_lock:
                    self.estatisticas_transferencia['chunks_enviados'] += 1
        except FileNotFoundError:
            print(f"Arquivo {filename} não encontrado para envio")
        except Exception as e:
            print(f"Erro ao enviar chunk: {str(e)}")

    def processar_chunk_recebido(self, filename, chunk_index, encoded_data):
        with self.download_lock:
            if not self.download_em_andamento or self.download_em_andamento['nome_arquivo'] != filename:
                print(f"Chunk recebido para {filename}, mas não há download em andamento")
                return

            download = self.download_em_andamento

            # Verificar se chunk já foi recebido
            if download['chunks'][chunk_index] is not None:
                print(f"Chunk {chunk_index} duplicado para {filename}")
                return

            # Decodificar e armazenar
            try:
                decoded_data = base64.b64decode(encoded_data.encode())
                download['chunks'][chunk_index] = decoded_data
                download['chunks_recebidos'] += 1

                # Atualizar estatísticas de transferência
                with self.clock_lock:
                    self.estatisticas_transferencia['chunks_recebidos'] += 1
                    self.estatisticas_transferencia['bytes_recebidos'] += len(encoded_data)

                print(
                    f"Chunk {chunk_index} de {filename} recebido ({download['chunks_recebidos']}/{download['num_chunks']})")

                # Verificar se download está completo
                if download['chunks_recebidos'] == download['num_chunks']:
                    self.finalizar_download(filename)
            except Exception as e:
                print(f"Erro ao processar chunk: {str(e)}")

    def finalizar_download(self, filename):
        with self.download_lock:
            if not self.download_em_andamento or self.download_em_andamento['nome_arquivo'] != filename:
                return

            download = self.download_em_andamento
            caminho = os.path.join(self.shared_dir, filename)

            try:
                # Medir tempo ANTES de escrever o arquivo (conforme especificação)
                tempo_total = time.time() - download['start_time']

                # Escrever arquivo no disco
                with open(caminho, 'wb') as f:
                    for chunk in download['chunks']:
                        f.write(chunk)

                # Registrar estatísticas de desempenho
                key = (
                    download['chunk_size'],
                    len(download['peers']),
                    download['tamanho']
                )
                self.estatisticas_download[key].append(tempo_total)

                print(f"\nDownload do arquivo {filename} finalizado em {tempo_total:.5f}s.")
                print(f"Estatísticas registradas: chunk={download['chunk_size']}, "
                      f"peers={len(download['peers'])}, size={download['tamanho']}")

                # Resetar download
                self.download_em_andamento = None
            except Exception as e:
                print(f"Erro ao salvar arquivo {filename}: {str(e)}")

    def process_ls_list(self, peer, file_data):
        self.update_peer_status(peer, "ONLINE", self.clock)
        num = int(file_data[0])
        arquivos = []
        for i in range(1, num + 1):
            nome, tamanho = file_data[i].split(':')
            arquivos.append((nome, int(tamanho)))
        self.arquivos_recebidos[peer] = arquivos

    def create_ls_list_response(self):
        arquivos = []
        for f in os.listdir(self.shared_dir):
            caminho = os.path.join(self.shared_dir, f)
            if os.path.isfile(caminho):
                tamanho = os.path.getsize(caminho)
                arquivos.append(f"{f}:{tamanho}")
        return f"{self.full_address} {self.clock} LS_LIST {len(arquivos)} {' '.join(arquivos)}"

    def alterar_chunk_size(self):
        print("Digite novo tamanho de chunk:")
        try:
            novo_tamanho = int(input("> "))
            if novo_tamanho <= 0:
                print("Tamanho deve ser um número positivo.")
                return
            self.chunk_size = novo_tamanho
            print(f"Tamanho de chunk alterado: {self.chunk_size}")
        except ValueError:
            print("Valor inválido. Deve ser um número inteiro.")

    def exibir_estatisticas(self):
        print("\n=== Estatísticas de Transferência ===")
        print(f"Chunks enviados: {self.estatisticas_transferencia['chunks_enviados']}")
        print(f"Chunks recebidos: {self.estatisticas_transferencia['chunks_recebidos']}")
        print(f"Bytes enviados: {self.estatisticas_transferencia['bytes_enviados']}")
        print(f"Bytes recebidos: {self.estatisticas_transferencia['bytes_recebidos']}")

        if self.download_em_andamento:
            download = self.download_em_andamento
            progresso = (download['chunks_recebidos'] / download['num_chunks']) * 100
            print(f"\nDownload em andamento: {download['nome_arquivo']}")
            print(f"Progresso: {progresso:.1f}% ({download['chunks_recebidos']}/{download['num_chunks']} chunks)")
            tempo_decorrido = time.time() - download['start_time']
            print(f"Tempo decorrido: {tempo_decorrido:.2f}s")

        print("\n=== Estatísticas de Desempenho ===")
        print("Tam. chunk | N peers | Tam. arquivo | N | Tempo [s] | Desvio")

        if not self.estatisticas_download:
            print("Nenhum dado estatístico de download disponível")
            return

        # Processar e ordenar estatísticas
        processed = []
        for key, tempos in self.estatisticas_download.items():
            chunk_size, num_peers, file_size = key
            n = len(tempos)
            media = sum(tempos) / n
            variancia = sum((x - media) ** 2 for x in tempos) / n
            desvio = math.sqrt(variancia) if n > 1 else 0.0

            processed.append((
                chunk_size,
                num_peers,
                file_size,
                n,
                media,
                desvio
            ))

        # Ordenar por chunk_size, num_peers e file_size
        processed.sort(key=lambda x: (x[0], x[1], x[2]))

        # Exibir resultados formatados
        for stat in processed:
            chunk_size, num_peers, file_size, n, media, desvio = stat
            print(f"{chunk_size:10d} | {num_peers:7d} | {file_size:12d} | {n:1d} | {media:.5f} | {desvio:.5f}")

    def list_peers(self):
        print("Lista de peers:")
        print("[0] voltar para o menu anterior")
        peers = list(self.peers.items())
        for i, (peer, info) in enumerate(peers, 1):
            print(f"[{i}] {peer} {info['status']} {info['clock']}")

        choice = int(input("> "))
        if choice == 0:
            return

        selected_peer = peers[choice - 1][0]
        self.increment_clock()
        message = f"{self.full_address} {self.clock} HELLO"
        self.send_message(selected_peer, message)

    def get_peers(self):
        self.increment_clock()
        message = f"{self.full_address} {self.clock} GET_PEERS"
        for peer in self.peers:
            self.send_message(peer, message)

    def list_local_files(self):
        print("Arquivos locais:")
        for f in os.listdir(self.shared_dir):
            if os.path.isfile(os.path.join(self.shared_dir, f)):
                print(f)

    def exit(self):
        self.increment_clock()
        message = f"{self.full_address} {self.clock} BYE"
        for peer, info in self.peers.items():
            if info["status"] == "ONLINE":
                self.send_message(peer, message)
        self.running = False
        print("Saindo...")

    def _listen(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_message, args=(conn, addr)).start()
            except:
                # Lidar com erros de socket ao encerrar
                if self.running:
                    print("Erro ao aceitar conexão")
                pass

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()

        while self.running:
            print("\nEscolha um comando:")
            print("[1] Listar peers")
            print("[2] Obter peers")
            print("[3] Listar arquivos locais")
            print("[4] Buscar arquivos")
            print("[5] Exibir estatisticas")
            print("[6] Alterar tamanho de chunk")
            print("[9] Sair")
            choice = input("> ")

            if choice == '1':
                self.list_peers()
            elif choice == '2':
                self.get_peers()
            elif choice == '3':
                self.list_local_files()
            elif choice == '4':
                self.buscar_arquivos()
            elif choice == '5':
                self.exibir_estatisticas()
            elif choice == '6':
                self.alterar_chunk_size()
            elif choice == '9':
                self.exit()
                break
            else:
                print("Comando inválido")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        print("Uso: python eachare.py <endereco:porta> <vizinhos.txt> <diretorio_compartilhado>")
        sys.exit(1)

    address, port = sys.argv[1].split(':')
    peer = EacharePeer(address, int(port), sys.argv[2], sys.argv[3])
    peer.start()