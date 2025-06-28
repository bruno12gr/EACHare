import os
import socket
import threading
import time
import base64
import math
import traceback
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
        self.chunk_size = 256  # Valor padrão conforme especificação

        # Garantir que o diretório compartilhado existe
        os.makedirs(shared_dir, exist_ok=True)

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

        # Variáveis para download em andamento
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
                print(f"Encaminhando mensagem \"{message[:60]}...\" para {destination}")

                # Atualizar estatísticas
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

        # SOLUÇÃO: Criar cópia antes de iterar
        peers_copy = list(self.peers.items())
        for peer, info in peers_copy:
            if info["status"] == "ONLINE":
                self.send_message(peer, message)

        time.sleep(2)

        arquivos_agrupados = {}
        for peer, arquivos in self.arquivos_recebidos.items():
            for nome, tamanho in arquivos:
                chave = (nome, tamanho)
                if chave not in arquivos_agrupados:
                    arquivos_agrupados[chave] = []
                arquivos_agrupados[chave].append(peer)

        lista_arquivos = []
        for (nome, tamanho), peers in arquivos_agrupados.items():
            lista_arquivos.append((nome, tamanho, peers))

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

        arquivo_selecionado = lista_arquivos[escolha - 1]
        nome_arquivo = arquivo_selecionado[0]
        tamanho_arquivo = arquivo_selecionado[1]
        peers_com_arquivo = arquivo_selecionado[2]

        print(f" arquivo escolhido {nome_arquivo} ")
        self.iniciar_download_fragmentado(nome_arquivo, tamanho_arquivo, peers_com_arquivo)

    def iniciar_download_fragmentado(self, nome_arquivo, tamanho_arquivo, peers):
        # Verificar se há peers disponíveis
        if not peers:
            print("Erro: Nenhum peer disponível para download do arquivo.")
            return

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

            # Codificar o nome do arquivo em base64 para evitar problemas com espaços e acentos
            filename_encoded = base64.b64encode(nome_arquivo.encode()).decode()

            self.increment_clock()
            msg = f"{self.full_address} {self.clock} DL {filename_encoded} {self.chunk_size} {chunk_index}"
            self.send_message(peer_selecionado, msg)
            print(f"Solicitando chunk {chunk_index} de {nome_arquivo} para {peer_selecionado}")

    def handle_message(self, conn, addr):
        try:
            # Aumentar buffer para 1MB para receber chunks grandes
            data = conn.recv(1024 * 1024).decode()
            if not data:
                return

            # Atualizar estatísticas de transferência
            with self.clock_lock:
                self.estatisticas_transferencia['bytes_recebidos'] += len(data)

            # Mostrar apenas parte da mensagem para não poluir a saída
            preview = data[:60] + "..." if len(data) > 60 else data
            print(f"Resposta recebida: \"{preview}\"")

            parts = data.strip().split()
            if len(parts) < 3:
                print("Mensagem mal formatada")
                return

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
                if len(parts) < 6:
                    print("Mensagem DL mal formatada")
                    return
                filename_encoded = parts[3]
                chunk_size = int(parts[4])
                chunk_index = int(parts[5])
                
                # Decodificar o nome do arquivo de base64
                try:
                    filename = base64.b64decode(filename_encoded.encode()).decode()
                except Exception as e:
                    print(f"Erro ao decodificar nome do arquivo: {str(e)}")
                    return
                
                self.enviar_chunk_arquivo(origin, filename, chunk_size, chunk_index)
            elif msg_type == "FILE":
                if len(parts) < 7:
                    print("Mensagem FILE mal formatada")
                    return
                filename_encoded = parts[3]
                chunk_size = int(parts[4])
                chunk_index = int(parts[5])
                encoded_data = ' '.join(parts[6:])  # Juntar todos os tokens restantes
                
                # Decodificar o nome do arquivo de base64
                try:
                    filename = base64.b64decode(filename_encoded.encode()).decode()
                except Exception as e:
                    print(f"Erro ao decodificar nome do arquivo: {str(e)}")
                    return
                
                self.processar_chunk_recebido(filename, chunk_index, encoded_data)

        except Exception as e:
            print(f"Erro ao processar mensagem: {str(e)}")
        finally:
            conn.close()

    def enviar_chunk_arquivo(self, destination, filename, chunk_size, chunk_index):
        caminho = os.path.join(self.shared_dir, filename)
        try:
            # Verificar se o arquivo existe
            if not os.path.exists(caminho):
                print(f"Arquivo {filename} não encontrado para envio")
                return

            with open(caminho, 'rb') as f:
                # Posicionar no início do chunk
                f.seek(chunk_index * chunk_size)

                # Ler o chunk (último pode ser menor)
                data = f.read(chunk_size)
                if not data:
                    print(f"Chunk {chunk_index} vazio para {filename}")
                    return

                encoded = base64.b64encode(data).decode()

                # Codificar o nome do arquivo em base64 para a resposta
                filename_encoded = base64.b64encode(filename.encode()).decode()

                self.increment_clock()
                msg = f"{self.full_address} {self.clock} FILE {filename_encoded} {chunk_size} {chunk_index} {encoded}"
                self.send_message(destination, msg)

                # Atualizar estatísticas de transferência
                with self.clock_lock:
                    self.estatisticas_transferencia['chunks_enviados'] += 1
        except FileNotFoundError:
            print(f"Arquivo {filename} não encontrado para envio")
        except Exception as e:
            print(f"Erro ao enviar chunk: {str(e)}")

    def processar_chunk_recebido(self, filename, chunk_index, encoded_data):
        print(f"Processando chunk {chunk_index} para {filename}")

        with self.download_lock:
            # Se não há download em andamento, criar um novo
            if not self.download_em_andamento or self.download_em_andamento['nome_arquivo'] != filename:
                print(f"Download não iniciado para {filename}, criando estrutura temporária")

                # Criar estrutura de download temporária
                self.download_em_andamento = {
                    'nome_arquivo': filename,
                    'tamanho': 0,
                    'num_chunks': 1,
                    'chunks_recebidos': 0,
                    'chunks': [None],
                    'start_time': time.time(),
                    'peers': ["Ad-hoc"],
                    'chunk_size': self.chunk_size
                }

            download = self.download_em_andamento

            # Garantir que o array de chunks tenha tamanho suficiente
            if chunk_index >= len(download['chunks']):
                download['chunks'] = download['chunks'] + [None] * (chunk_index - len(download['chunks']) + 1)
                download['num_chunks'] = len(download['chunks'])

            # Verificar se chunk já foi recebido
            if download['chunks'][chunk_index] is not None:
                print(f"Chunk {chunk_index} duplicado para {filename}. Ignorando.")
                return

            # Decodificar e armazenar
            try:
                decoded_data = base64.b64decode(encoded_data.encode())
                download['chunks'][chunk_index] = decoded_data
                download['chunks_recebidos'] += 1

                # Atualizar tamanho total do arquivo
                download['tamanho'] = sum(len(chunk) for chunk in download['chunks'] if chunk is not None)

                # Atualizar estatísticas
                with self.clock_lock:
                    self.estatisticas_transferencia['chunks_recebidos'] += 1
                    self.estatisticas_transferencia['bytes_recebidos'] += len(encoded_data)

                print(
                    f"Chunk {chunk_index} de {filename} recebido ({download['chunks_recebidos']}/{download['num_chunks']})")

                # Verificar se download está completo
                chunks_nao_nulos = sum(1 for chunk in download['chunks'] if chunk is not None)
                
                if chunks_nao_nulos == download['num_chunks']:
                    print("Todos os chunks recebidos, finalizando download...")
                    self.finalizar_download(filename)
                else:
                    print(f"Faltam {download['num_chunks'] - chunks_nao_nulos} chunks")
            except Exception as e:
                print(f"Erro ao processar chunk: {str(e)}")
                import traceback
                traceback.print_exc()

    def finalizar_download(self, filename):
        print(f"Iniciando finalização do download para {filename}")

        if not self.download_em_andamento or self.download_em_andamento['nome_arquivo'] != filename:
            print(f"Finalizar download: nenhum download em andamento para {filename}.")
            return

        download = self.download_em_andamento

        # Verificar se todos os chunks foram recebidos
        chunks_nao_nulos = sum(1 for chunk in download['chunks'] if chunk is not None)
        if chunks_nao_nulos != download['num_chunks']:
            print(f"Download incompleto! Recebidos {chunks_nao_nulos}/{download['num_chunks']} chunks")
            return

        # Usar caminho absoluto para evitar problemas de diretório
        caminho = os.path.abspath(os.path.join(self.shared_dir, filename))
        print(f"Salvando arquivo em: {caminho}")

        try:
            tempo_total = time.time() - download['start_time']

            # Garantir que o diretório existe
            os.makedirs(os.path.dirname(caminho), exist_ok=True)

            # Escrever arquivo
            with open(caminho, 'wb') as f:
                for chunk in download['chunks']:
                    if chunk is not None:
                        f.write(chunk)

            # Verificar se o arquivo foi criado
            if os.path.exists(caminho):
                file_size = os.path.getsize(caminho)
                print(f"Arquivo salvo com sucesso! Tamanho: {file_size} bytes")

                # Registrar estatísticas
                key = (
                    download['chunk_size'],
                    len(download['peers']),
                    file_size  # Usar tamanho real do arquivo salvo
                )
                self.estatisticas_download[key].append(tempo_total)

                print(f"Download do arquivo {filename} finalizado em {tempo_total:.5f}s.")
            else:
                print("Erro: Arquivo não foi criado após escrita!")

            # Resetar download
            self.download_em_andamento = None
        except Exception as e:
            print(f"Erro crítico ao salvar arquivo {filename}: {str(e)}")
            import traceback
            traceback.print_exc()

    def process_ls_list(self, peer, file_data):
        self.update_peer_status(peer, "ONLINE", self.clock)

        try:
            num = int(file_data[0])
        except:
            print("Formato inválido para número de arquivos")
            return

        arquivos = []
        index = 1  # Começa após o número de arquivos

        for _ in range(num):
            if index >= len(file_data):
                break

            # Combina todos os elementos até encontrar um que contenha ':'
            file_str = file_data[index]
            index += 1
            while ':' not in file_str and index < len(file_data):
                file_str += " " + file_data[index]
                index += 1

            # Divide apenas no último ':' (para lidar com nomes que contenham ':')
            if ':' in file_str:
                parts = file_str.rsplit(':', 1)
                if len(parts) == 2:
                    nome, tamanho_str = parts
                    try:
                        tamanho = int(tamanho_str)
                        arquivos.append((nome, tamanho))
                    except ValueError:
                        print(f"Tamanho inválido para arquivo: {nome}")
                else:
                    print(f"Formato inválido para arquivo: {file_str}")
            else:
                print(f"Formato inválido para arquivo: {file_str}")

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
            # Calcular desvio padrão amostral se n > 1, caso contrário 0
            if n > 1:
                variancia = sum((x - media) ** 2 for x in tempos) / (n - 1)
                desvio = math.sqrt(variancia)
            else:
                desvio = 0.0

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
        # SOLUÇÃO: Criar cópia antes de iterar
        peers_copy = list(self.peers.keys())
        for peer in peers_copy:
            self.send_message(peer, message)

    def list_local_files(self):
        print("Arquivos locais:")
        for f in os.listdir(self.shared_dir):
            if os.path.isfile(os.path.join(self.shared_dir, f)):
                print(f)

    def exit(self):
        self.increment_clock()
        message = f"{self.full_address} {self.clock} BYE"
        # SOLUÇÃO: Criar cópia antes de iterar
        peers_copy = list(self.peers.items())
        for peer, info in peers_copy:
            if info["status"] == "ONLINE":
                self.send_message(peer, message)
        self.running = False
        print("Saindo...")

    def _listen(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_message, args=(conn, addr)).start()
            except Exception as e:
                if self.running:
                    print(f"Erro ao aceitar conexão: {str(e)}")

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()

        while self.running:
            time.sleep(0.1)
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