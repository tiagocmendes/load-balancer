# coding: utf-8

import socket
import select
import signal
import pickle
import logging
import time
import argparse

# configure logger output format
logging.basicConfig(level=logging.DEBUG,format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',datefmt='%m-%d %H:%M:%S')
logger = logging.getLogger('Load Balancer')


# used to stop the infinity loop
done = False


# implements a graceful shutdown
def graceful_shutdown(signalNumber, frame):  
    logger.debug('Graceful Shutdown...')
    global done
    done = True


class N2One:
    def __init__(self, servers):
        self.servers = servers  

    def select_server(self):
        logger.debug("N20ne")
        return self.servers[0]

    def update(self, args):
        pass

# round robin policy
class RoundRobin:
    def __init__(self, servers):
        self.servers = servers
        self.next = -1;

    def select_server(self):
        logger.debug("RoundRobin")
        self.next += 1
        return self.servers[self.next % len(self.servers)]

    def update(self, args):
        pass


# least connections policy
class LeastConnections:
    def __init__(self, servers):
        self.servers = servers
        self.connections = {server: 0 for server in servers}

    def select_server(self):
        return min(self.connections, key=self.connections.get)

    def update(self, args):
        if args['action'] == 'ADD':
            self.connections[args['server']] += 1
        else:
            self.connections[args['server']] -= 1
        logger.debug('No. of connections: %s', self.connections)

        


# least response time
class LeastResponseTime:
    def __init__(self, servers):
        self.servers = servers
        self.timestamps = {}
        self.response_times = {server:0 for server in servers}

    def select_server(self):
        selected_server = min(self.response_times, key=self.response_times.get)
        logger.debug('Selected server: %s, response time: %s', selected_server, self.response_times[selected_server])
        return selected_server

    def update(self, args):
        if args['action'] == 'ADD':
            self.timestamps[(args['server'], args['client'])] = time.time()
        else:
            self.timestamps[(args['server'], args['client'])] = time.time() - self.timestamps[(args['server'], args['client'])]
            aux_dict = {}
            for key in self.timestamps:  
                if key[0] not in aux_dict:
                    aux_dict[key[0]] = [self.timestamps[key]]
                else:
                    aux_dict[key[0]].append(self.timestamps[key])
            for key in aux_dict:
                total_time = sum(aux_dict[key])
                # mean
                self.response_times[key] = total_time / len(aux_dict[key])

class Cache:
    def __init__(self):
        self.cache = {}
    def check_cache(self, key):
        if key in self.cache:
            return self.cache[key] 
        else:
            return None
        
    def update_cache(self, key, data):
        self.cache[key] = data
    

class SocketMapper:
    def __init__(self, policy):
        self.policy = policy
        self.map = {}
        self.connections = {}
    def add(self, client_sock, upstream_server):
        upstream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream_sock.connect(upstream_server)
        logger.debug("Proxying to %s %s", *upstream_server)
        self.map[client_sock] =  upstream_sock
        self.connections[client_sock] = upstream_server
        self.policy.update({'action': 'ADD', 'server': upstream_server, 'client': client_sock})

 

    def delete(self, sock):
        try:
            self.map.pop(sock)
            upstream_server = self.connections.pop(sock)
            self.policy.update({'action': 'REMOVE', 'server': upstream_server, 'client': sock})
            sock.close() 
        except KeyError:
            pass

    def get_sock(self, sock):
        for c, u in self.map.items():
            if u == sock:
                return c
            if c == sock:
                return u
        return None

    def get_all_socks(self):
        """ Flatten all sockets into a list"""
        return list(sum(self.map.items(), ())) 


def main(addr, servers,cache):
    # register handler for interruption 
    # it stops the infinite loop gracefully
    signal.signal(signal.SIGINT, graceful_shutdown)

    # choose policy
    p = 'LRT'
    if p == 'N2O':
        policy = N2One(servers)
    elif p == 'RR':
        policy = RoundRobin(servers)
    elif p == 'LC':
        policy = LeastConnections(servers)
    elif p == 'LRT':
        policy = LeastResponseTime(servers)

    mapper = SocketMapper(policy)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.setblocking(False)
        sock.bind(addr)
        sock.listen()
        logger.debug("Listening on %s %s", *addr)
        while not done:
            readable, writable, exceptional = select.select([sock]+mapper.get_all_socks(), [], [], 1)
            if readable is not None:
                for s in readable:
                    if s == sock:
                        client, addr = sock.accept()
                        logger.debug("Accepted connection %s %s", *addr)
                        client.setblocking(False)
                        server = policy.select_server()
                        mapper.add(client, server)
                    if mapper.get_sock(s):
                        data = s.recv(4096)
                        if len(data) == 0: # No messages in socket, we can close down the socket
                            mapper.delete(s)
                        else:
                            mapper.get_sock(s).send(data)


    except Exception as err:
        logger.error(err)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pi HTTP server')
    parser.add_argument('-p', dest='port', type=int, help='load balancer port', default=8080)
    parser.add_argument('-s', dest='servers', nargs='+', type=int, help='list of servers ports')
    args = parser.parse_args()
    
    servers = []
    for p in args.servers:
        servers.append(('localhost', p))
    
    cache = Cache()
    
    main(('127.0.0.1', args.port), servers, cache)
