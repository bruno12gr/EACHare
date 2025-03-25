import os
import socket
import threading
import time
from datetime import datetime

class EacharePeer:
    def __init__(self, address, port, neighbors_file, shared_dir):
        self.address = address
        self.port = port
        self.full_address = f"{address}:{port}"
        self.peers = {}  # {address: (status, last_updated)}
        self.clock = 0
        self.clock_lock = threading.Lock()
        self.shared_dir = shared_dir
        self.running = True

        # Carregar peers iniciais
        with open(neighbors_file, 'r') as f:
            for line in f:
                peer = line.strip()
                if peer:
                    self.peers[peer] = "OFFLINE"
                    print(f"Adicionando novo peer {peer} status OFFLINE")

        # Configurar socket do servidor
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((address, port))
        self.server_socket.listen(5)

    def increment_clock(self):
        with self.clock_lock:
            self.clock += 1
            print(f"=> Atualizando relogio para {self.clock}")

    def handle_message(self, conn, addr):
        data = conn.recv(1024).decode()
        if not data:
            return

        print(f"Mensagem recebida: \"{data}\"")
        self.increment_clock()

        parts = data.strip().split()
        origin = parts[0]
        msg_clock = int(parts[1])
        msg_type = parts[2]

        if msg_type == "HELLO":
            self.update_peer_status(origin, "ONLINE")
        elif msg_type == "GET_PEERS":
            response = self.create_peer_list_response(origin)
            self.send_message(origin, response)
        elif msg_type == "PEER_LIST":
            self.process_peer_list(parts[3:])
        elif msg_type == "BYE":
            self.update_peer_status(origin, "OFFLINE")

        conn.close()

    def update_peer_status(self, peer, status):
        if peer in self.peers:
            if self.peers[peer] != status:
                print(f"Atualizando peer {peer} status {status}")
                self.peers[peer] = status
        else:
            print(f"Adicionando novo peer {peer} status {status}")
            self.peers[peer] = status

    def send_message(self, destination, message):
        try:
            addr, port = destination.split(':')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((addr, int(port)))
                s.sendall(message.encode())
                print(f"Encaminhando mensagem \"{message}\" para {destination}")
                self.update_peer_status(destination, "ONLINE")
        except Exception as e:
            print(f"Erro ao conectar em {destination}: {str(e)}")
            self.update_peer_status(destination, "OFFLINE")

    def create_peer_list_response(self, exclude_peer):
        peer_list = []
        for peer in self.peers:
            if peer != exclude_peer:
                peer_list.append(f"{peer}:{self.peers[peer]}:0")
        return f"{self.full_address} {self.clock} PEER_LIST {len(peer_list)} {' '.join(peer_list)}"

    def process_peer_list(self, peers_data):
        count = int(peers_data[0])
        for i in range(1, count+1):
            peer_info = peers_data[i].split(':')
            peer_addr = f"{peer_info[0]}:{peer_info[1]}"
            status = peer_info[2]
            self.update_peer_status(peer_addr, status)

    def list_peers(self):
        print("Lista de peers:")
        print("[0] voltar para o menu anterior")
        peers = list(self.peers.items())
        for i, (peer, status) in enumerate(peers, 1):
            print(f"[{i}] {peer} {status}")

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
        for peer, status in self.peers.items():
            if status == "ONLINE":
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
            print("[9] Sair")
            choice = input("> ")

            if choice == '1':
                self.list_peers()
            elif choice == '2':
                self.get_peers()
            elif choice == '3':
                self.list_local_files()
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