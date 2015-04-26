#!/usr/bin/env python

'''
Ravello external inventory script
=================================

Generates inventory that Ansible can understand by making API request to
Ravellosystems using the ravello_sdk (https://github.com/ravello/python-sdk) library.

NOTE: This script assumes Ansible is being executed where the environment
variables have already been set:
    export RAVELLO_USERNAME='AK123'
    export RAVELLO_PASSWORD='abc123'

This script also assumes there is an ravello.ini file alongside it. To specify a
different path to ravello.ini, define the RAVELLO_INI_PATH environment variable:

    export RAVELLO_INI_PATH=/path/to/my_rav.ini

If you're using babu you need to set the above variables and
you need to define:

    export RAVELLO_URL=https://hostname_of_your_babu/api/v1

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
    rav_id
    rav_app_owner_name
    rav_description
    rav_app_active_vms
 
Instances are grouped by the following categories:

    rav_app_id
    rav_app_name
    rav_region_id
    rav_cloud_id
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
    def _empty_inventory(self):
        return {"_meta" : {"hostvars" : {}}}

    def __init__(self):
        ''' Main execution path '''
       
        self.inventory = self._empty_inventory()

        # Read settings and parse CLI arguments
        self.read_settings()
        self.parse_cli_args()

        # single app
        if self.args.app:
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
        
        config = ConfigParser.SafeConfigParser()
        rav_default_ini_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'ravello.ini')
        rav_ini_path = os.environ.get('RAVELLO_INI_PATH', rav_default_ini_path)
        config.read(rav_ini_path)

        self.ssh_service_name = config.get('rav', 'ssh_service_name','ssh')
        self.destination_variable = config.get('rav', 'destination_variable','fqdn')
        self.ssh_user_name = config.get('rav', 'ssh_user_name','ravello')
        username = os.environ.get('RAVELLO_USERNAME')
        password = os.environ.get('RAVELLO_PASSWORD')
        url = os.environ.get('RAVELLO_URL')
        
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
                (dest,data) = self.get_host_info_dict(app,vm)
                if not dest:
                    continue
                
                self.inventory["_meta"]["hostvars"][dest] = data
               # group by
                self.push(self.inventory, self.to_safe('rav_app_id_'+ str(app['id'])), dest)
                self.push(self.inventory, self.to_safe('rav_app_name_'+ app['name']), dest)
                self.push(self.inventory, self.to_safe('rav_region_id_'+ app['deployment']['regionId']), dest)
                self.push(self.inventory, self.to_safe('rav_cloud_id_'+ app['deployment']['cloudId']), dest)
                self.push(self.inventory, self.to_safe('rav_vm_name_'+ vm['name']), dest)
                vm_name = self.to_safe(vm['name'])
                m = re.search('^(.*)-\d*?$',vm_name)
                if m == None:
                    group_name = vm_name
                else:
                    group_name = m.groups()[0]
                
                self.push(self.inventory, self.to_safe('rav_vm_group_'+ group_name), dest)
                
                if 'keypairName' in vm:
                    self.push(self.inventory, self.to_safe('rav_ssh_keypair_name_'+ vm['keypairName']), dest)
                
                self.push(self.inventory, self.to_safe('rav_publish_optimization_'+ str(app['deployment']['publishOptimization'])), dest)
                # Global Tag: tag all ravello vms (in all apps)
                self.push(self.inventory, 'rav_organization', dest)
            except Exception, e:
                print 'error add to inventory. for app: %s ,vm: %s, EX: %s' % (app['name'], vm['name'], e)
           
    def is_external_ssh_service(self,supplied_service):
        return (supplied_service['name'].lower() == self.ssh_service_name.lower() or supplied_service['portRange'].split(",")[0].split("-")[0] == "22") and supplied_service['external'] == True
    
    def get_host_info_dict(self, app, vm):
        instance_vars = {}
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
        instance_vars['rav_cloud_id'] = app['deployment']['cloudId']
        
        instance_vars['rav_name'] = vm['name']
        instance_vars['rav_id'] = vm['id']
        
        instance_vars['rav_description'] = None
        if 'description' in vm:
            instance_vars['rav_description'] = vm['description']
        
        instance_vars['rav_owner_name'] = app['owner']
        
        # ssh port determination        
        for supplied_service in vm.get('suppliedServices', []):
            ext = self.is_external_ssh_service(supplied_service)
            if ext:
                for network_connection in vm.get('networkConnections', []):
                    if network_connection['ipConfig']['id'] == supplied_service['ipConfigLuid']:
                        if self.destination_variable in network_connection['ipConfig']:
                            dest = network_connection['ipConfig'][self.destination_variable]
                        else:
                            self.error("%s field is not supported" % self.destination_variable )
                        instance_vars['rav_public_ip'] = network_connection['ipConfig']['publicIp']
                        
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
    
        if 'rav_ssh_port' not in instance_vars or dest is None:
            return None, {}

        return dest, instance_vars

    def push(self, my_dict, key, element):
        ''' Push an element onto an array that may not have been defined in the dict '''
        group_info = my_dict.setdefault(key, [])
        if isinstance(group_info, dict):
            host_list = group_info.setdefault('hosts', [])
            host_list.append(element)
        else:
            group_info.append(element)

    def to_safe(self, word):
        ''' Converts 'bad' characters in a string to underscores so they can be
        used as Ansible groups '''

        return re.sub("[^A-Za-z0-9\-]", "_", word)


    def json_format_dict(self, data, pretty=False):
        ''' Converts a dict to a JSON object and dumps it as a formatted
        string '''

        if pretty:
            return json.dumps(data, sort_keys=True, indent=2)
        else:
            return json.dumps(data)

    def error(self,msg):
        print(msg)
        sys.exit(1)
# Run the script
RavelloInventory()

