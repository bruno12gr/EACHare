import os
import socket
import threading
import time
import base64
import math

class EacharePeer:
    def __init__(self, address, port, neighbors_file, shared_dir):
        self.address = address
        self.port = port
        self.full_address = f"{address}:{port}"
        self.peers = {}
        self.clock = 0
        self.clock_lock = threading.Lock()
        self.peers_lock = threading.Lock()  # Lock para proteger acesso aos peers
        self.arquivos_lock = threading.Lock()  # Lock para proteger arquivos_recebidos
        self.chunks_lock = threading.Lock()  # Lock para proteger chunks_recebidos
        self.stats_lock = threading.Lock()  # Lock para proteger estatísticas
        self.shared_dir = shared_dir
        self.running = True
        self.neighbors_file = neighbors_file
        self.arquivos_recebidos = {}  # {peer: [(nome, tamanho), ...]}
        self.chunk_size = 256
        self.chunks_recebidos = {}  # {filename: {chunk_index: True/False}}
        self.download_stats = {}  # {(chunk_size, num_peers, file_size): [tempos]}
        self.stats = {
            "downloads_iniciados": 0,
            "downloads_concluidos": 0,
            "bytes_baixados": 0,
            "bytes_enviados": 0,
            "pedidos_recebidos": 0
        }

        # Carrega vizinhos com tratamento de erro
        try:
            with open(neighbors_file, 'r') as f:
                for line in f:
                    peer = line.strip()
                    if peer and peer != self.full_address:
                        self.peers[peer] = {"status": "OFFLINE", "clock": 0}
                        print(f"Adicionando novo peer {peer} status OFFLINE")
        except FileNotFoundError:
            print(f"Arquivo de vizinhos '{neighbors_file}' não encontrado. Iniciando com lista vazia.")

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Permite reutilizar a porta
        self.server_socket.bind((address, port))
        self.server_socket.listen(5)

    def increment_clock(self):
        with self.clock_lock:
            self.clock += 1
            print(f"=> Atualizando relogio para {self.clock}")

    def handle_message(self, conn, addr):
        try:
            data = conn.recv(1024 * 100).decode()
            if not data:
                return

            # Trunca mensagens FILE para não poluir a saída
            if "FILE" in data:
                parts = data.split()
                if len(parts) >= 7:
                    # Mostra apenas o início da mensagem FILE
                    display_data = f"{parts[0]} {parts[1]} FILE {parts[3]} {parts[4]} {parts[5]} {parts[6][:20]}..."
                    print(f"Resposta recebida: \"{display_data}\"")
                else:
                    print(f"Resposta recebida: \"{data}\"")
            else:
                print(f"Resposta recebida: \"{data}\"")

            parts = data.strip().split()
            # if len(parts) < 3:
            #     print(f"Mensagem malformada recebida: {data}")
            #     return
                
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
                if len(parts) >= 6:  # DL com chunk: filename chunk_size chunk_index
                    filename = parts[3]
                    chunk_size = int(parts[4])
                    chunk_index = int(parts[5])
                    self.stats["pedidos_recebidos"] += 1
                    self.send_chunk_response(origin, filename, chunk_size, chunk_index)
                elif len(parts) >= 4:  # DL antigo: filename
                    filename = parts[3]
                    self.send_file_response(origin, filename)
            elif msg_type == "FILE":
                if len(parts) >= 7:  # FILE com chunk: filename chunk_size chunk_index encoded_data
                    filename = parts[3]
                    chunk_size = int(parts[4])
                    chunk_index = int(parts[5])
                    encoded_data = parts[6]
                    self.save_chunk_to_file(filename, chunk_size, chunk_index, encoded_data)
                elif len(parts) >= 7:  # FILE antigo: filename 0 0 encoded_data
                    filename = parts[3]
                    encoded_data = parts[6]
                    self.save_downloaded_file(filename, encoded_data)

        except (ValueError, IndexError) as e:
            print(f"Erro ao processar mensagem: {e}")
        except Exception as e:
            print(f"Erro inesperado ao processar mensagem: {e}")
        finally:
            conn.close()

    def create_ls_list_response(self):
        arquivos = []
        try:
            for f in os.listdir(self.shared_dir):
                caminho = os.path.join(self.shared_dir, f)
                if os.path.isfile(caminho):
                    tamanho = os.path.getsize(caminho)
                    arquivos.append(f"{f}:{tamanho}")
        except OSError as e:
            print(f"Erro ao listar arquivos do diretório: {e}")
            
        return f"{self.full_address} {self.clock} LS_LIST {len(arquivos)} {' '.join(arquivos)}"

    def process_ls_list(self, peer, file_data):
        self.update_peer_status(peer, "ONLINE", self.clock)  # <- ADICIONE ESTA LINHA
        try:
            num = int(file_data[0])
            arquivos = []
            for i in range(1, min(num + 1, len(file_data))):  # Protege contra índice fora do range
                nome, tamanho = file_data[i].split(':')
                arquivos.append((nome, int(tamanho)))
            
            with self.arquivos_lock:
                self.arquivos_recebidos[peer] = arquivos
        except (ValueError, IndexError) as e:
            print(f"Erro ao processar lista de arquivos de {peer}: {e}")

    def buscar_arquivos(self):
        with self.arquivos_lock:
            self.arquivos_recebidos = {}
        self.increment_clock()
        message = f"{self.full_address} {self.clock} LS"

        online_peers = []
        with self.peers_lock:
            for peer, info in self.peers.items():
                if info["status"] == "ONLINE":
                    online_peers.append(peer)

        for peer in online_peers:
            self.send_message(peer, message)

        time.sleep(2)  # tempo para receber respostas

        # Agrupa arquivos por nome e tamanho
        arquivos_agrupados = {}  # {(nome, tamanho): [peer1, peer2, ...]}
        
        with self.arquivos_lock:
            for peer, arquivos in self.arquivos_recebidos.items():
                for nome, tamanho in arquivos:
                    chave = (nome, tamanho)
                    if chave not in arquivos_agrupados:
                        arquivos_agrupados[chave] = []
                    arquivos_agrupados[chave].append(peer)

        if not arquivos_agrupados:
            print("Nenhum arquivo encontrado.")
            return

        print("\nArquivos encontrados na rede:")
        print("Nome | Tamanho | Peer")
        
        # Lista os arquivos agrupados
        todos_arquivos = []
        for i, ((nome, tamanho), peers) in enumerate(arquivos_agrupados.items(), 1):
            todos_arquivos.append((nome, tamanho, peers))
            peers_str = ", ".join(peers)
            print(f"[{i:2}] {nome} | {tamanho} | {peers_str}")

        print("[0] Cancelar")
        try:
            escolha = int(input("Digite o numero do arquivo para fazer o download:\n"))
        except ValueError:
            print("Entrada inválida. Cancelando busca.")
            return

        if escolha == 0:
            return

        if 1 <= escolha <= len(todos_arquivos):
            nome, tamanho, peers = todos_arquivos[escolha - 1]
            print(f"Você selecionou: {nome} de {peers[0]}")

            # Inicia o download fragmentado
            self.stats["downloads_iniciados"] += 1
            self.download_file_chunked(nome, tamanho, peers)
        else:
            print("Opção inválida. Cancelando download.")

    def download_file_chunked(self, filename, file_size, available_peers):
        # Inicia a medição de tempo
        start_time = time.time()
        
        # Calcula o número de chunks
        num_chunks = (file_size + self.chunk_size - 1) // self.chunk_size
        
        print(f"arquivo escolhido {filename}")
        
        # Inicializa o controle de chunks recebidos
        with self.chunks_lock:
            self.chunks_recebidos[filename] = {}
            for i in range(num_chunks):
                self.chunks_recebidos[filename][i] = False
        
        # Inicializa o arquivo de destino
        file_path = os.path.join(self.shared_dir, filename)
        with open(file_path, 'wb') as f:
            f.truncate(file_size)  # Cria o arquivo com o tamanho correto
        
        # Distribui os chunks entre os peers (round-robin)
        for chunk_index in range(num_chunks):
            # Escolhe o peer para este chunk (round-robin)
            peer_index = chunk_index % len(available_peers)
            selected_peer = available_peers[peer_index]
            
            # Envia solicitação de chunk
            self.increment_clock()
            msg = f"{self.full_address} {self.clock} DL {filename} {self.chunk_size} {chunk_index}"
            self.send_message(selected_peer, msg)
            
            # Aguarda um pouco para não sobrecarregar a rede
            time.sleep(0.1)
        
        # Aguarda todos os chunks serem recebidos
        max_wait_time = 30  # 30 segundos máximo
        wait_time = 0
        while wait_time < max_wait_time:
            with self.chunks_lock:
                if filename in self.chunks_recebidos:
                    all_received = all(self.chunks_recebidos[filename].values())
                    if all_received:
                        break
            
            time.sleep(0.5)
            wait_time += 0.5
        
        # Finaliza a medição de tempo (antes de escrever em disco)
        end_time = time.time()
        download_time = end_time - start_time
        
        # Verifica se todos os chunks foram recebidos
        with self.chunks_lock:
            if filename in self.chunks_recebidos:
                missing_chunks = [i for i, received in self.chunks_recebidos[filename].items() if not received]
                if missing_chunks:
                    print(f"Aviso: Chunks não recebidos: {missing_chunks}")
                else:
                    print(f"Download do arquivo {filename} finalizado.")
                    self.stats["downloads_concluidos"] += 1
                    
                    # Registra estatísticas de tempo
                    self.record_download_stats(self.chunk_size, len(available_peers), file_size, download_time)
                    
                    # Remove o controle de chunks para liberar memória
                    del self.chunks_recebidos[filename]

    def record_download_stats(self, chunk_size, num_peers, file_size, download_time):
        key = (chunk_size, num_peers, file_size)
        with self.stats_lock:
            if key not in self.download_stats:
                self.download_stats[key] = []
            self.download_stats[key].append(download_time)

    def calculate_stats(self, times):
        if not times:
            return 0.0, 0.0
        
        n = len(times)
        mean = sum(times) / n
        
        if n == 1:
            return mean, 0.0
        
        variance = sum((x - mean) ** 2 for x in times) / (n - 1)
        std_dev = math.sqrt(variance)
        
        return mean, std_dev

    def show_stats(self):
        print("Tam. chunk | N peers | Tam. arquivo | N | Tempo [s] | Desvio")
        
        with self.stats_lock:
            for (chunk_size, num_peers, file_size), times in self.download_stats.items():
                mean, std_dev = self.calculate_stats(times)
                n = len(times)
                print(f"{chunk_size} | {num_peers} | {file_size} | {n} | {mean:.5f} | {std_dev:.5f}")

    def send_file_response(self, destination, filename):
        caminho = os.path.join(self.shared_dir, filename)
        try:
            with open(caminho, 'rb') as f:
                data = f.read()
                encoded = base64.b64encode(data).decode()
                self.increment_clock()
                msg = f"{self.full_address} {self.clock} FILE {filename} 0 0 {encoded}"
                self.send_message(destination, msg)
        except FileNotFoundError:
            print(f"Arquivo {filename} não encontrado para envio")
        except Exception as e:
            print(f"Erro ao enviar arquivo {filename}: {e}")

    def send_chunk_response(self, destination, filename, chunk_size, chunk_index):
        caminho = os.path.join(self.shared_dir, filename)
        try:
            with open(caminho, 'rb') as f:
                # Calcula a posição do chunk
                start_pos = chunk_index * chunk_size
                f.seek(start_pos)
                
                # Lê o chunk
                chunk_data = f.read(chunk_size)
                
                if chunk_data:  # Se há dados para enviar
                    encoded = base64.b64encode(chunk_data).decode()
                    self.increment_clock()
                    msg = f"{self.full_address} {self.clock} FILE {filename} {chunk_size} {chunk_index} {encoded}"
                    self.send_message(destination, msg)
                    self.stats["bytes_enviados"] += len(chunk_data)
                    print(f"Enviando chunk {chunk_index} de {filename} ({len(chunk_data)} bytes)")
                else:
                    print(f"Chunk {chunk_index} de {filename} não encontrado")
                    
        except FileNotFoundError:
            print(f"Arquivo {filename} não encontrado para envio de chunk")
        except Exception as e:
            print(f"Erro ao enviar chunk {chunk_index} de {filename}: {e}")

    def save_downloaded_file(self, filename, encoded_data):
        try:
            decoded = base64.b64decode(encoded_data.encode())
            caminho = os.path.join(self.shared_dir, filename)
            with open(caminho, 'wb') as f:
                f.write(decoded)
            print(f"Download do arquivo {filename} finalizado.")
            self.start()
        except Exception as e:
            print(f"Erro ao salvar o arquivo {filename}: {str(e)}")

    def save_chunk_to_file(self, filename, chunk_size, chunk_index, encoded_data):
        try:
            decoded = base64.b64decode(encoded_data.encode())
            caminho = os.path.join(self.shared_dir, filename)
            
            # Abre o arquivo em modo de escrita binária e posiciona no local correto
            with open(caminho, 'r+b') as f:
                start_pos = chunk_index * chunk_size
                f.seek(start_pos)
                f.write(decoded)
            
            self.stats["bytes_baixados"] += len(decoded)
            
            # Marca o chunk como recebido
            with self.chunks_lock:
                if filename in self.chunks_recebidos:
                    self.chunks_recebidos[filename][chunk_index] = True
            
        except Exception as e:
            print(f"Erro ao salvar chunk {chunk_index} de {filename}: {e}")

    def update_peer_status(self, peer, status, pClock):
        with self.peers_lock:
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
                self.update_peer_status(destination, "ONLINE", self.clock)
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f"Erro ao conectar em {destination}: {str(e)}")
            self.update_peer_status(destination, "OFFLINE", self.clock)
        except Exception as e:
            print(f"Erro inesperado ao enviar mensagem para {destination}: {e}")

    def create_peer_list_response(self, exclude_peer):
        peer_list = []
        with self.peers_lock:
            for peer, info in self.peers.items():
                if peer != exclude_peer:
                    peer_list.append(f"{peer}:{info['status']}:{info['clock']}")
        return f"{self.full_address} {self.clock} PEER_LIST {len(peer_list)} {' '.join(peer_list)}"

    def process_peer_list(self, peers_data):
        try:
            count = int(peers_data[0])
            for i in range(1, min(count + 1, len(peers_data))):  # Protege contra índice fora do range
                peer_info = peers_data[i].split(':')
                if len(peer_info) >= 4:  # Verifica se tem todos os campos necessários
                    peer_addr = f"{peer_info[0]}:{peer_info[1]}"
                    status = peer_info[2]
                    clock = int(peer_info[3])
                    self.add_peer(peer_addr)
                    self.update_peer_status(peer_addr, status, clock)
        except (ValueError, IndexError) as e:
            print(f"Erro ao processar lista de peers: {e}")

    def is_peer_in_file(self, peer_address):
        with self.peers_lock:
            return peer_address in self.peers

    def add_peer(self, peer_address):
        if not self.is_peer_in_file(peer_address):
            self.add_peer_to_neighbors_file(peer_address)

    def add_peer_to_neighbors_file(self, peer_address):
        try:
            # Verifica se o peer já existe no arquivo para evitar duplicatas
            existing_peers = set()
            try:
                with open(self.neighbors_file, 'r') as f:
                    for line in f:
                        existing_peers.add(line.strip())
            except FileNotFoundError:
                pass  # Arquivo não existe ainda, será criado
            
            if peer_address not in existing_peers:
                with open(self.neighbors_file, 'a') as file:
                    file.write(f"\n{peer_address}")
        except Exception as e:
            print(f"Erro ao adicionar peer ao arquivo: {e}")

    def list_peers(self):
        print("Lista de peers:")
        print("[0] voltar para o menu anterior")
        with self.peers_lock:
            peers = list(self.peers.items())
        
        for i, (peer, info) in enumerate(peers, 1):
            print(f"[{i}] {peer} {info['status']} {info['clock']}")

        try:
            choice = int(input("> "))
        except ValueError:
            print("Entrada inválida. Voltando ao menu.")
            return

        if choice == 0:
            return

        if 1 <= choice <= len(peers):
            selected_peer = peers[choice-1][0]
            self.increment_clock()
            message = f"{self.full_address} {self.clock} HELLO"
            self.send_message(selected_peer, message)
        else:
            print("Opção inválida.")

    def get_peers(self):
        self.increment_clock()
        message = f"{self.full_address} {self.clock} GET_PEERS"
        with self.peers_lock:
            all_peers = list(self.peers.keys())
        
        for peer in all_peers:
            self.send_message(peer, message)

    def list_local_files(self):
        print("Arquivos locais:")
        try:
            for f in os.listdir(self.shared_dir):
                if os.path.isfile(os.path.join(self.shared_dir, f)):
                    print(f)
        except OSError as e:
            print(f"Erro ao listar arquivos locais: {e}")

    def change_chunk_size(self):
        try:
            new_size_str = input("Digite novo tamanho de chunk:\n")
            new_size = int(new_size_str)
            self.chunk_size = new_size
            print(f"Tamanho de chunk alterado: {self.chunk_size}")
        except ValueError:
            print("Entrada inválida.")

    def exit(self):
        self.increment_clock()
        message = f"{self.full_address} {self.clock} BYE"
        with self.peers_lock:
            peers_copy = dict(self.peers)  # Cria uma cópia para iterar com segurança
        
        for peer, info in peers_copy.items():
            if info["status"] == "ONLINE":
                self.send_message(peer, message)
        
        self.running = False
        print("Saindo...")

    def _listen(self):
        self.server_socket.settimeout(1.0)  # Timeout para permitir verificação de self.running
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_message, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue  # Volta para verificar self.running
            except Exception as e:
                if self.running:
                    print(f"Erro no listener: {e}")
                break

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        
        while self.running:
            print("\nEscolha um comando:")
            print("[1] Listar peers")
            print("[2] Obter peers")
            print("[3] Listar arquivos locais")
            print("[4] Buscar Arquivos")
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
                self.show_stats()
            elif choice == '6':
                self.change_chunk_size()
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
