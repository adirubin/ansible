#!/usr/bin/env python

'''
Ravello external inventory script
=================================

Generates inventory that Ansible can understand by making API request to
Ravellosystems using the ravello_sdk (https://github.com/ravello/python-sdk) library.

NOTE: This script assumes Ansible is being executed where the environment variables have already been set:
    export RAVELLO_USERNAME='AK123'
    export RAVELLO_PASSWORD='abc123'

if you're want to change SSH_SERVICE_NAME or SSH_USER_NAME you need to define:
    export RAVELLO_SSH_SERVICE_NAME=ssh
    export RAVELLO_SSH_USER_NAME=ubuntu

If you're using babu you need to set the above variables and you need to define:
    export RAVELLO_URL=https://hostname_of_your_babu/api/v1

If you're want to limit result to 1 app you need to define:
    export RAVELLO_APP_NAME=app_name
    or
    RAVELLO_APP_NAME=app_name ansible-playbook -i ravello.py site.yml

This script returns the following variables in hostvars (per host):

    rav_fqdn
    ansible_ssh_port
    rav_ssh_port
    ansible_ssh_user
    rav_ssh_user_name
    rav_ssh_keypair_name
    rav_private_ip
    rav_app_expiration_time
    rav_public_ip
    rav_app_id
    rav_app_name
    rav_name
    rav_group
    rav_id
    rav_app_owner_name
    rav_description
    rav_app_active_vms
 
Instances are grouped by the following categories:

    rav_app_id
    rav_app_name
    rav_region_id
    rav_vm_group
    rav_vm_name
    rav_publish_optimization
    rav_organization
    rav_key_pair_name


Note: the values are case sensitive!
 '''

# (c) 2015, ravellosystems by zoza
#
# This file is part of Ansible,
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

######################################################################

import sys
import os
import argparse
import re
from time import time
import ConfigParser
from collections import defaultdict

try:
    import json
except ImportError:
    import simplejson as json

try:
    from ravello_sdk import *
except ImportError:
    print "ravello_sdk (https://github.com/ravello/python-sdk) required for this module"


class RavelloInventory(object):

    def __init__(self):
        self.ssh_service_name = None
        self.ssh_user_name = None
        self.app_name_limit = None
        self.client = None
        self.inventory = {"_meta": {"hostvars": {}}}
        self.args = None

        # Read settings and parse CLI arguments
        self.read_settings()
        self.parse_cli_args()

        # single app or limit
        if self.args.app or self.app_name_limit is not None:
            if self.app_name_limit is not None:
                app = self.client.get_application_by_name(self.app_name_limit)
            else:
                app = self.client.get_application_by_name(self.args.app)
                
            self.add_app_to_inventory(app)
            
        elif self.args.list:
            apps = self.get_apps()
            for app in apps:
                # reload full app
                app = self.client.get_application(app)
                self.add_app_to_inventory(app)
            
        print self.json_format_dict(self.inventory, True)
   
    def read_settings(self):

        self.ssh_service_name = os.environ.get('RAVELLO_SSH_SERVICE_NAME', 'ssh')
        self.ssh_user_name = os.environ.get('RAVELLO_SSH_USER_NAME', 'ubuntu')
        username = os.environ['RAVELLO_USERNAME']
        password = os.environ['RAVELLO_PASSWORD']
        url = os.environ.get('RAVELLO_URL')
        self.app_name_limit = os.environ.get('RAVELLO_APP_NAME')
        self.client = RavelloClient(username, password, url)
       
    def parse_cli_args(self):
        ''' Command line argument processing '''

        parser = argparse.ArgumentParser(description='Produce an Ansible Inventory file based on Ravello')
        parser.add_argument('--list', action='store_true', default=True,
                           help='List of applications (default: True)')
        parser.add_argument('--app', action='store',
                           help='Get all the variables about hosts in a specific application')
        self.args = parser.parse_args()

    def get_apps(self):
        return self.client.get_applications(lambda app: (app['published'] and int(app['deployment']['totalActiveVms']) > 0))

    def add_app_to_inventory(self, app):
        ''' Adds a vms of app to the inventory, as long as it is addressable '''
        
        for vm in app['deployment']['vms']:
            if vm['state'] != "STARTED":
                continue
            
            try:
                (dest, data) = self.get_host_info_dict(app, vm)
                if not dest:
                    continue
                
                self.inventory["_meta"]["hostvars"][dest] = data
                # group by
                self.push(self.inventory, self.to_safe('rav_app_id_' + str(app['id'])), dest)
                self.push(self.inventory, self.to_safe('rav_app_name_' + app['name']), dest)
                self.push(self.inventory, self.to_safe('rav_region_id_' + app['deployment']['regionId']), dest)
                self.push(self.inventory, self.to_safe('rav_vm_name_' + vm['name']), dest)
                
                if 'os' in vm:
                    self.push(self.inventory, self.to_safe('rav_vm_os_' + vm['os']), dest)
                
                group_name = self.get_group_name_by_vm_name(vm['name'])
                self.push(self.inventory, self.to_safe('rav_vm_group_' + group_name), dest)
                
                if 'keypairName' in vm:
                    self.push(self.inventory, self.to_safe('rav_ssh_keypair_name_' + vm['keypairName']), dest)
                
                self.push(self.inventory, self.to_safe('rav_publish_optimization_' + str(app['deployment']['publishOptimization'])), dest)
                # Global Tag: tag all ravello vms (in all apps)
                self.push(self.inventory, 'rav_organization', dest)
            except Exception, e:
                print 'error add to inventory. for app: %s ,vm: %s, EX: %s' % (app['name'], vm['name'], e)
           
    def is_external_ssh_service(self, sup_service):
        return sup_service['name'].lower() == self.ssh_service_name.lower() and sup_service['external'] is True
    
    def get_host_info_dict(self, app, vm):
        instance_vars = dict()
        #app stuff
        instance_vars['rav_app_active_vms'] = int(app['deployment']['totalActiveVms'])
        instance_vars['rav_app_id'] = app['id']
        instance_vars['rav_app_name'] = app['name']
        
        instance_vars['rav_app_expiration_time'] = -1
        if 'expirationTime' in app['deployment']:
            instance_vars['rav_app_expiration_time'] = app['deployment']['expirationTime']
        
        instance_vars['rav_ssh_keypair_name'] = None
        if 'keypairName' in vm:
            instance_vars['rav_ssh_keypair_name'] = vm['keypairName']
        
        instance_vars['ansible_ssh_user'] = self.ssh_user_name
        instance_vars['rav_ssh_user_name'] = self.ssh_user_name
        
        instance_vars['rav_region_id'] = app['deployment']['regionId']

        instance_vars['rav_is_windows'] = False
        if 'os' in vm:
            if "windows" in vm['os']:
                instance_vars['ansible_connection'] = 'winrm' 
                instance_vars['rav_is_windows'] = True
        
        instance_vars['rav_name'] = vm['name']
        instance_vars['rav_group'] = self.get_group_name_by_vm_name(vm['name'])
        instance_vars['rav_id'] = vm['id']
        if 'hostnames' in vm:
            instance_vars['rav_hostname'] = vm['hostnames'][0]
        
        instance_vars['rav_description'] = None
        if 'description' in vm:
            instance_vars['rav_description'] = vm['description']
        
        instance_vars['rav_owner_name'] = app['owner']
        
        # ssh port determination
        for supplied_service in vm.get('suppliedServices', []):
            if self.is_external_ssh_service(supplied_service):
                for network_connection in vm.get('networkConnections', []):
                    if network_connection['ipConfig']['id'] == supplied_service['ipConfigLuid']:
                        dest = network_connection['ipConfig'].get('fqdn')
                        instance_vars['rav_public_ip'] = network_connection['ipConfig'].get('publicIp')
                        if 'autoIpConfig' in network_connection['ipConfig']:
                            if 'allocatedIp' in network_connection['ipConfig']['autoIpConfig']:
                                private_ip = network_connection['ipConfig']['autoIpConfig']['allocatedIp']
                            elif 'reservedIp' in network_connection['ipConfig']['autoIpConfig']:
                                private_ip = network_connection['ipConfig']['autoIpConfig']['reservedIp']
                        elif 'staticIpConfig' in network_connection['ipConfig']:
                            private_ip= network_connection['ipConfig']['staticIpConfig']['ip']
                        instance_vars['rav_private_ip'] = private_ip
                        
                        instance_vars['rav_fqdn'] = network_connection['ipConfig']['fqdn']
                instance_vars['rav_ssh_port'] = int(supplied_service['externalPort'].split(",")[0].split("-")[0])
                instance_vars['ansible_ssh_port'] = int(supplied_service['externalPort'].split(",")[0].split("-")[0])
                break

        if 'rav_ssh_port' not in instance_vars or dest is None:
            return None, {}

        return dest, instance_vars

    @staticmethod
    def push(my_dict, key, element):
        ''' Push an element onto an array that may not have been defined in the dict '''
        group_info = my_dict.setdefault(key, [])
        if isinstance(group_info, dict):
            host_list = group_info.setdefault('hosts', [])
            host_list.append(element)
        else:
            group_info.append(element)

    @staticmethod
    def json_format_dict(data, pretty=False):
        ''' Converts a dict to a JSON object and dumps it as a formatted
        string '''

        if pretty:
            return json.dumps(data, sort_keys=True, indent=2)
        else:
            return json.dumps(data)

    @staticmethod
    def error(msg):
        print(msg)
        sys.exit(1)

    @staticmethod
    def get_group_name_by_vm_name(vm_name):
        vm = RavelloInventory.to_safe(vm_name)
        m = re.search('^(.*)_\d*?$', vm)
        if m is None:
            return vm
        else:
            return m.groups()[0]

    @staticmethod
    def to_safe(word_to_check):
        ''' Converts 'bad' characters in a string to underscores so they can be
        used as Ansible groups '''

        return re.sub("[^A-Za-z0-9\_]", "_", word_to_check)


# Run the script
RavelloInventory()

