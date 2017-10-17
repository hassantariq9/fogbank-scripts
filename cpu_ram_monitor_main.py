"""
A Multithreaded HTTP server that collects utilisation stats from
the Hadoop nodes specified in $HADOOP_HOME/etc/hadoop/slaves
"""

import os
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn
from threading import Thread, Timer
import json
import time
import cgi
import socket
import fcntl
import requests
import psutil

PORT = 12345
monitoring_active = False
PATHS = ["/start-monitoring", "/end-monitoring", "/push-stats"]
directory = ""

class HTTPHandler(BaseHTTPRequestHandler):

    def _set_headers(self, code, ctype):
        """
        Set up HTTP headers
        """
        self.send_response(code)
        self.send_header('Content-type', ctype)
        self.end_headers()

    def _read_data(self):
        # check if received data is JSON
        ctype, _ = cgi.parse_header(self.headers.getheader('content-type'))
        if ctype != 'application/json':
            self._set_headers(400, 'text/html')
            self.wfile.write('POST data is not a JSON object\n')
            return None

        content_length = int(self.headers.getheader('content-length'))
        post_data = self.rfile.read(content_length)
        return json.loads(post_data)

    def do_POST(self):
        """
        Handle a HTTP POST message
        """
        global monitoring_active, directory
        #check path
        if not self.path in PATHS:
            self._set_headers(404, 'text/html')
            self.wfile.write('Path not found\n')
            return

        if self.path == PATHS[0]:
            json_data = self._read_data()
            if json_data is None:
                return
            directory = json_data['directory']

            to_monitor = ["cpu_percent", "virtual_memory", "disk_usage"]
            for stm in to_monitor:
                with open(os.path.join(directory, 'indv_ports_' + stm + '.csv'), 'w') as f:
                    f.write('name,tags,time,value\n')

            #start monitoring stats
            self.modify_monitoring(PATHS[0])
            monitoring_active = True
            self.monitor()
        elif self.path == PATHS[1]:
            #stop monitoring
            self.modify_monitoring(PATHS[1])
            monitoring_active = False
        else:
            #parse the JSON object
            json_data = self._read_data()
            if json_data is None:
                return
            #get the hostname from the IP address of sender
            host = socket.gethostbyaddr(self.client_address[0])
            self.write_stats(host[0], json_data)

        #got here so code hasn't returned from errors
        self._set_headers(200, 'text/html')
        self.wfile.write('Success')

    def write_stats(self, client, content):
        """
        Writes the stats into a file in the given directory.
        The time is a unix timestamp in nanoseconds.
        """
        for key, value in content.items():
            line = '{},host={},{:19d},{}\n'.format(key, client, int(time.time()*1000000000), value)
            with open(os.path.join(directory, 'indv_ports_' + key + '.csv'), 'a') as f:
                #obtain lock on file and write
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(line)
                fcntl.flock(f, fcntl.LOCK_UN)


    def modify_monitoring(self, path):
        """
        Either start or end monitoring. This depends on the path
        """
        slaves_file = os.path.join(os.environ["HADOOP_HOME"], "etc", "hadoop", "slaves")
        slaves = []
        # get the slave hostnames
        with open(slaves_file) as f:
            for line in f:
                line = line.strip()
                #don't get this node's name if it is in the slaves file
                if line != os.uname()[1]:
                    slaves.append(line)

        #send a request to all the slaves using threads so the time between
        #each request to the nodes is minimised.
        for slave in slaves:
            t = Thread(target=self.send_request, args=(slave, path,))
            t.start()

    def log_message(self, format, *args):
        return

    def send_request(self, slave, path):
        """
        Send a HTTP POST request to a slave
        """
        url = "http://{}:{}{}".format(slave, PORT, path)
        requests.post(url)

    def monitor(self):
        """
        Monitor own stats every 1 second.
        """
        if not monitoring_active:
            return
        #create a thread to start after 1 second
        Timer(1.0, self.monitor).start()

        #get CPU, RAM, and harddisk stats
        stats = {}
        stats["cpu_percent"] = psutil.cpu_percent()
        stats["virtual_memory"] = psutil.virtual_memory().percent
        stats["disk_usage"] = psutil.disk_usage("/").percent
        self.write_stats(os.uname()[1], stats)

#use a threaded server so that it can handle multiple requests at once
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    pass

#start up the server
if __name__ == "__main__":
    server = ThreadedHTTPServer(('', PORT), HTTPHandler)
    print "starting server"
    server.serve_forever()
