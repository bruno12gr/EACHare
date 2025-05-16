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
        self.peers = {}  # {address: {"status": "ONLINE"/"OFFLINE", "clock": int}}
        self.clock = 0
        self.clock_lock = threading.Lock()
        self.shared_dir = shared_dir
        self.running = True
        self.neighbors_file = neighbors_file

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
        data = conn.recv(1024).decode()
        if not data:
            return

        print(f"Mensagem recebida: \"{data}\"")

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

        conn.close()

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
            print(f"[{i}] {peer} {info['status']} (clock={info['clock']})")

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
            print ("[4] Buscar Arquivos")
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
