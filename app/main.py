
import os
import shlex
import subprocess
from threading import Thread
import time
import argparse
import logging

import yaml

from pssh.clients.ssh import SSHClient

from watchdog.observers import Observer
from watchdog.observers.api import EventQueue
from watchdog.events import FileSystemEventHandler


parser = argparse.ArgumentParser()
parser.add_argument("-c", "--config", type=str, required=True, help="Define the config.yaml file")
args = parser.parse_args()

config_file = args.config

file_yaml = open(config_file, "r")
config = yaml.load(file_yaml, Loader=yaml.SafeLoader)

ssh_user = config["ssh_user"]
ssh_pkey = config["ssh_pkey"]
cimgs_src_path = config["cimgs_src_path"]
pves_list = config["pves_list"]


handler  = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s|%(levelname)s|%(name)s|%(message)s"))

logger = logging.getLogger("mktemplate")
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

class Watcher:
    def __init__(self, path, queue):
        self.observer = Observer()
        self.path = path
        self.queue = queue

    def run(self):
        event_handler = Handler()
        self.observer.schedule(event_handler, self.path, recursive=True)
        self.observer.start()
        try:
            while True:
                time.sleep(5)
                e = queue.get()
                self.copy_file(e)
        except:
            self.observer.stop()
            logger.error("Observer stoped")

        self.observer.join()
    
    def copy_file(self, e):
        for pve in pves_list:
            worker = Thread(target=sync, args=(e, pve))
            worker.setDaemon(True)
            worker.start()    

class Handler(FileSystemEventHandler):
    def __init__(self, *args, **kwargs):
        super(FileSystemEventHandler, self).__init__(*args, **kwargs)
        self.last_created = None

    def on_modified(self, event):
        if event.is_directory:
            return None
        path = event.src_path        
        if path != self.last_created:
            logger.info("Modified file: %s", event.src_path)
            self.last_created = path
            # queue.put(event)

    def on_closed(self, event):
        if event.is_directory:
            return None
        logger.info("Closed file: %s", event.src_path)
        queue.put(event)

    def on_deleted(self, event):
        if event.is_directory:
            return None
        path = event.src_path
        if path == self.last_created:
            self.last_created = None
            logger.info("Deleted file: {}".format(event.src_path))

def sync(e, pve):
    host, port, cimgs_dst_path = pve.split(":")
    file_src_path = e.src_path
    command = "rsync --perms --times --group --owner --partial --dirs --verbose --rsync-path='sudo rsync' --rsh 'ssh -o StrictHostKeyChecking=no -i {} -p {}' {} {}@{}:{}"
    command = command.format(ssh_pkey, port, file_src_path, ssh_user, host, cimgs_dst_path)
    args = shlex.split(command)
    logger.debug("Started sync {} on {}".format(file_src_path, host))
    resp = subprocess.run(args, text=True, capture_output=True)
    if resp.returncode == 0:
        create_template(file_src_path, pve)
    else:
        logger.debug("Sync failed on: {} > {}".format(host, resp.stderr.encode("utf-8")))

def create_template(file_src_path, pve):
    host, port, cimgs_dst_path = pve.split(":")
    logger.debug("Creating template on: {}".format(host))
    base_path, file = os.path.split(file_src_path)
    file_name, file_ext = os.path.splitext(file)
    vmid, ostype, vmname = file_name.split("_")
    file_dst_path = os.path.join(cimgs_dst_path, file)
    commands = [
        "/usr/sbin/qm create {0} --name {1} --memory 2048 --net0 virtio,bridge=vmbr602".format(vmid, vmname),
        "/usr/sbin/qm importdisk {0} {1} SHARED_STORAGE --format {2}".format(vmid, file_dst_path, file_ext[1:]),
        "/usr/sbin/qm set {0} --ide0 media=cdrom,file=none".format(vmid),
        "/usr/sbin/qm set {0} --ide2 SHARED_STORAGE:cloudinit".format(vmid),
        "/usr/sbin/qm set {0} --boot c --bootdisk scsi0".format(vmid),
        "/usr/sbin/qm set {0} --serial1 socket --vga std".format(vmid),
        "/usr/sbin/qm set {0} --ostype {1} --agent 1".format(vmid, ostype),
        "/usr/sbin/qm set {0} --scsihw virtio-scsi-pci --scsi0 SHARED_STORAGE:vm-{1}-disk-0".format(vmid, vmid),
        "/usr/sbin/qm template {0}".format(vmid),
    ]

    client = SSHClient(host=host, user=ssh_user, port=int(port), pkey=ssh_pkey, num_retries=2)

    command = "sudo bash -c '/usr/bin/pvesh get /cluster/nextid -vmid {}'".format(vmid)
    out = client.run_command(command)
    if len(list(out.stderr)) > 0:
        command = "/usr/sbin/pvesm free SHARED_STORAGE:base-{}-disk-0 ; /usr/sbin/pvesm free SHARED_STORAGE:vm-{}-cloudinit ; /usr/sbin/qm destroy {} ; sleep 1".format(vmid, vmid, vmid)
        commands.insert(0, command)
    command = "sudo bash -c '{}'".format(" && ".join(commands))
    # print(command)
    out = client.run_command(command)
    stderr = list(out.stderr)
    if len(stderr) > 0:
        if "uninitialized" in "".join(stderr):
            logger.debug("Template created on: {}".format(host))
        else:
            logger.debug("Error creating template on: {}".format(host))
            logger.debug(stderr)
        return
    logger.debug("Template created on: {}".format(host))


if __name__ == "__main__":
    logger.debug("Starting..".format(time.asctime()))
    queue = EventQueue()
    w = Watcher(cimgs_src_path, queue)
    w.run()