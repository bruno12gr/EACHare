import os
import socket
import threading
import time
import base64
from datetime import datetime

class EacharePeer:
    def __init__(self, address, port, neighbors_file, shared_dir):
        self.address = address
        self.port = port
        self.full_address = f"{address}:{port}"
        self.peers = {}  # {address: {"status": "ONLINE"/"OFFLINE", "clock": int}}
        self.clock = 0
        self.clock_lock = threading.Lock()
        self.shared_dir = shared_dir
        self.running = True
        self.neighbors_file = neighbors_file
        self.arquivos_recebidos = {}  # {peer: [(nome, tamanho), ...]}

        with open(neighbors_file, 'r') as f:
            for line in f:
                peer = line.strip()
                if peer and peer != self.full_address:
                    self.peers[peer] = {"status": "OFFLINE", "clock": 0}
                    print(f"Adicionando novo peer {peer} status OFFLINE")

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((address, port))
        self.server_socket.listen(5)

    def increment_clock(self):
        with self.clock_lock:
            self.clock += 1
            print(f"=> Atualizando relogio para {self.clock}")

    def handle_message(self, conn, addr):
        data = conn.recv(1024 * 100).decode()
        if not data:
            return

        print(f"Resposta recebida: \"{data}\"")

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
            self.send_file_response(origin, filename)
        elif msg_type == "FILE":
            filename = parts[3]
            encoded_data = parts[6]
            self.save_downloaded_file(filename, encoded_data)

        conn.close()

    def create_ls_list_response(self):
        arquivos = []
        for f in os.listdir(self.shared_dir):
            caminho = os.path.join(self.shared_dir, f)
            if os.path.isfile(caminho):
                tamanho = os.path.getsize(caminho)
                arquivos.append(f"{f}:{tamanho}")
        return f"{self.full_address} {self.clock} LS_LIST {len(arquivos)} {' '.join(arquivos)}"

    def process_ls_list(self, peer, file_data):
        self.update_peer_status(peer, "ONLINE", self.clock)  # <- ADICIONE ESTA LINHA
        num = int(file_data[0])
        arquivos = []
        for i in range(1, num + 1):
            nome, tamanho = file_data[i].split(':')
            arquivos.append((nome, int(tamanho)))
        self.arquivos_recebidos[peer] = arquivos

    def buscar_arquivos(self):
        self.arquivos_recebidos = {}
        self.increment_clock()
        message = f"{self.full_address} {self.clock} LS"

        for peer, info in self.peers.items():
            if info["status"] == "ONLINE":
                self.send_message(peer, message)

        time.sleep(2)  # tempo para receber respostas

        todos_arquivos = []
        for peer, arquivos in self.arquivos_recebidos.items():
            for nome, tamanho in arquivos:
                todos_arquivos.append((nome, tamanho, peer))

        if not todos_arquivos:
            print("Nenhum arquivo encontrado.")
            return

        print("\nArquivos encontrados na rede:")
        print("     Nome        | Tamanho  | Peer ")
        for i, (nome, tamanho, peer) in enumerate(todos_arquivos, 1):
            print(f"[{i}]  {nome}  | {tamanho}      | {peer}")

        print("[0] Cancelar")
        escolha = int(input("> "))
        if escolha == 0:
            return

        selecionado = todos_arquivos[escolha - 1]
        print(f"Você selecionou: {selecionado[0]} de {selecionado[2]}")

        self.increment_clock()
        msg = f"{self.full_address} {self.clock} DL {selecionado[0]} 0 0"
        self.send_message(selecionado[2], msg)

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
        for i in range(1, count+1):
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

    def list_peers(self):
        print("Lista de peers:")
        print("[0] voltar para o menu anterior")
        peers = list(self.peers.items())
        for i, (peer, info) in enumerate(peers, 1):
            print(f"[{i}] {peer} {info['status']} {info['clock']}")

        choice = int(input("> "))
        if choice == 0:
            return

        selected_peer = peers[choice-1][0]
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
            conn, addr = self.server_socket.accept()
            threading.Thread(target=self.handle_message, args=(conn, addr)).start()

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()

        while self.running:
            print("\nEscolha um comando:")
            print("[1] Listar peers")
            print("[2] Obter peers")
            print("[3] Listar arquivos locais")
            print("[4] Buscar Arquivos")
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