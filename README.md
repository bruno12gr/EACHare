# EACHare - Sistema de Compartilhamento Peer-to-Peer
por GRUPO 59 Alaina e Bruno
## Pré-requisitos
- **Python 3**: "sudo apt install python3" - Ignorar caso já tenha python instalado, dependendo da versão não precisa usar "python3" para rodar o código, "python" já basta
- **Portas TCP livres**: Garanta que as portas usadas pelos Peers estejam liberadas no firewall
- **Diretório compartilhado**: Crie um diretório com arquivos para compartilhamento
- **Arquivo de peers**: Arquivo `.txt` com lista inicial de peers (formato: `IP:PORTA`)

## Execução
### Comando Basico:
	python3 eachare.py <ENDEREÇO:PORTA> <ARQUIVO_VIZINHOS.txt> <DIRETÓRIO_COMPARTILHADO>

## Exemplo
### **Peer1**:
	python eachare.py 127.0.0.1:9001 peers1.txt ./diretorio_compartilhado1

### **Peer2**:
	python3 eachare.py 127.0.0.1:9002 peers_peer2.txt shared_dir2



