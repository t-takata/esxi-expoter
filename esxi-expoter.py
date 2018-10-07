#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fenc=utf-8 ff=unix ft=python ts=4 sw=4 sts=4 si et :

import os
import re
import string
import sys
import getopt
import subprocess
import urlparse
import datetime
import time
import BaseHTTPServer

class VSISHInterface(object):
    def cat(self, paths):
        raise NotImplementedError()
    def ls(self, path):
        raise NotImplementedError()
    def get_openports(self):
        raise NotImplementedError()

class VSISHExecutor(VSISHInterface):
    def cat(self, paths):
        commandline = ["vsish", "-p", "-e", "cat"]
        commandline.extend(paths)
        output = subprocess.check_output(commandline)
        matches = re.findall('(^{.*?^})', output, re.DOTALL | re.MULTILINE)

        if len(paths) != len(matches):
            return {}

        result = {}
        for i, path in enumerate(paths):
            data = eval(matches[i])
            result[path] = data

        return result

    def ls(self, path):
        return subprocess.check_output(["vsish", "-e", "ls", path])

    def get_openports(self):
        ports_str = self.ls("/net/openPorts")
        open_ports = map(lambda s:int(string.rstrip(s, "/")), ports_str.strip().split())
        return open_ports

class ESXiExpoter(object):
    ESXI_PORT_CLIENTTYPE_NONE = 0
    ESXI_PORT_CLIENTTYPE_PNIC = 4
    ESXI_PORT_CLIENTTYPE_VNIC = 5

    ESXI_PORT_CLIENTSUBTYPE_VNIC_E1000 = 7
    ESXI_PORT_CLIENTSUBTYPE_VNIC_VMXNET3 = 9

    class HTTPServer(BaseHTTPServer.HTTPServer, object):
        def __init__(self, server_address, RequestHandlerClass):
            self.logic = None
            super(ESXiExpoter.HTTPServer, self).__init__(server_address, RequestHandlerClass)

    class HTTPGetHandler(BaseHTTPServer.BaseHTTPRequestHandler, object):
        def __init__(self, callback, *args):
            super(ESXiExpoter.HTTPGetHandler, self).__init__(callback, *args)

        def do_GET(self):
            parsed_path = urlparse.urlparse(self.path)

            response_body = ""
            if parsed_path.path == "/metrics":
                self.send_response(200)
                metrics_text = self.server.logic.get_metrics()
                response_body = metrics_text
            else:
                self.send_response(404)
                
            self.end_headers()
            self.wfile.write(response_body)
            return

    def get_metrics(self):
        ports = self.vsish.get_openports()
        type_paths = map(lambda port:"/net/openPorts/%d/type" % (port), ports)
        types = self.vsish.cat(type_paths)

        status_paths = []
        for port in ports:
            type_path = "/net/openPorts/%d/type" % (port)
            portset_name = types[type_path]["portsetName"]
            status_paths.append("/net/portsets/%s/ports/%d/status" % (portset_name, port))
        status = self.vsish.cat(status_paths)

        data_paths = []
        for port in ports:
            type_path = "/net/openPorts/%d/type" % (port)
            portset_name = types[type_path]["portsetName"]
            status_path = "/net/portsets/%s/ports/%d/status" % (portset_name, port)
            data_paths.append("/net/portsets/%s/ports/%d/stats" % (portset_name, port))

            if status[status_path]["clientType"] == self.ESXI_PORT_CLIENTTYPE_VNIC:
                if status[status_path]["clientSubType"] == self.ESXI_PORT_CLIENTSUBTYPE_VNIC_VMXNET3:
                    data_paths.append("/net/portsets/%s/ports/%d/vmxnet3/rxSummary" % (portset_name, port))
                    data_paths.append("/net/portsets/%s/ports/%d/vmxnet3/txSummary" % (portset_name, port))
                #elif status[status_path]["clientSubType"] == self.ESXI_PORT_CLIENTSUBTYPE_VNIC_E1000:
                #    data_paths.append("/net/portsets/%s/ports/%d/e1000/rxQueueStats" % (portset_name, port))
                #    data_paths.append("/net/portsets/%s/ports/%d/e1000/txQueueStats" % (portset_name, port))
        data = self.vsish.cat(data_paths)

        esxi_hostname = os.uname()[1]
        now = datetime.datetime.now()
        timestamp = int(time.mktime(now.timetuple()))
        metrics = []
        for port in ports:
            type_path = "/net/openPorts/%d/type" % (port)
            portset_name = types[type_path]["portsetName"]
            status_path = "/net/portsets/%s/ports/%d/status" % (portset_name, port)
            stats_path = "/net/portsets/%s/ports/%d/stats" % (portset_name, port)

            port_status = status[status_path]
            port_stats = data[stats_path]
            client_name = port_status["clientName"]
            for k in port_stats:
                value = port_stats[k]
                metric_name = "esxi_" + k
                metric_str = '%s{esxi_name="%s",port_id="%d",client_name="%s"} %d' % (metric_name, esxi_hostname, port, client_name, value)
                metrics.append(metric_str)

            if port_status["clientType"] == self.ESXI_PORT_CLIENTTYPE_VNIC:
                if port_status["clientSubType"] == self.ESXI_PORT_CLIENTSUBTYPE_VNIC_VMXNET3:
                    rx_stats_path = "/net/portsets/%s/ports/%d/vmxnet3/rxSummary" % (portset_name, port)
                    tx_stats_path = "/net/portsets/%s/ports/%d/vmxnet3/txSummary" % (portset_name, port)

                    rx_stats = data[rx_stats_path]
                    tx_stats = data[tx_stats_path]
                    for k in rx_stats:
                        value = rx_stats[k]
                        metric_name = "esxi_vmxnet3_rx_" + k
                        metric_str = '%s{esxi_name="%s",port_id="%d",client_name="%s"} %d' % (metric_name, esxi_hostname, port, client_name, value)
                        metrics.append(metric_str)
                    for k in tx_stats:
                        value = tx_stats[k]
                        metric_name = "esxi_vmxnet3_tx_" + k
                        metric_str = '%s{esxi_name="%s",port_id="%d",client_name="%s"} %d' % (metric_name, esxi_hostname, port, client_name, value)
                        metrics.append(metric_str)
                        
                #elif status[status_path]["clientSubType"] == self.ESXI_PORT_CLIENTSUBTYPE_VNIC_E1000:
                #    rx_stats_path = "/net/portsets/%s/ports/%d/e1000/rxQueueStats" % (portset_name, port)
                #    tx_stats_path = "/net/portsets/%s/ports/%d/e1000/txQueueStats" % (portset_name, port)

        return "\n".join(metrics)

    def usage(self):
        print("usage: esxi-exporter.py [-p|--port PORT] [-l|--listen LISTENIP]")

    def main(self):
        options = {
            "port": 8080,
            "listen": "0.0.0.0",
        }
        self.vsish = VSISHExecutor()
        
        try:
            opts, args = getopt.getopt(sys.argv[1:], "hl:p:m", ["help", "listen=", "port="])
        except getopt.GetoptError as err:
            print str(err)
            self.usage()
            sys.exit(2)

        for o, a in opts:
            if o in ("-h", "--help"):
                self.usage()
                sys.exit()
            elif o in ("-p", "--port"):
                options["port"] = int(a)
            elif o in ("-l", "--listen"):
                options["listen"] = a

        server = self.HTTPServer((options["listen"], options["port"]), self.HTTPGetHandler)
        server.logic = self

        try:
            sys.stderr.write("Serving HTTP on %s port %d ...\n" % (options["listen"], options["port"]))
            server.serve_forever()
        except KeyboardInterrupt:
            pass
            sys.exit(0)

if __name__ == "__main__":
    ESXiExpoter().main()
