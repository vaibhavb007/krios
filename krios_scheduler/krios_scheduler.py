#!/usr/bin/env python

import time
from datetime import datetime
import random
import json

from k8s import *
from utils import *
from kubernetes import client, config, watch

config.load_incluster_config()
api=client.CoreV1Api()



satellites = read_tles("tles.txt")

def fetch_sat_id(node):
    sat_id = node.metadata.labels.get('sat_id1', None)
    if not sat_id:
        sat_id = node.metadata.labels['sat_id']

    return int(sat_id)

def filter_nodes(nodes, jd, fr, app_location, allowable_distance):
    candidate_nodes = {}
    for node in nodes:
        print("node_Name ", node.metadata.name)
        sat_id = fetch_sat_id(node)
        print(sat_id)
        l1 = satellites[sat_id]['line1']
        l2 = satellites[sat_id]['line2']
        satellite = Satrec.twoline2rv(l1, l2)
        e, node_location, node_velocity = satellite.sgp4(jd, fr)
        print(calculate_distance(node_location, app_location), allowable_distance)
        if(calculate_distance(node_location, app_location) < allowable_distance):
            candidate_nodes[node.metadata.name] = (node_velocity[0] * (app_location[0] - node_location[0])) + (node_velocity[1] * (app_location[1] - node_location[1])) + (node_velocity[2] * (app_location[2] - node_location[2]))

    return candidate_nodes            


def filter_sort(pod):
    if pod.spec.node_name is not None:
        return pod.spec.node_name

    nodes = get_follower_nodes(api)

    dt = datetime.fromtimestamp(time.time())
    print(dt)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    radius = pod.metadata.labels.get('radius', 100)
    app_location = parseLocation(pod.metadata.labels['leoregion'])
    app_location = geodetic2cartesian(app_location[0], app_location[1], 550000)
    print(radius, app_location)
    allowable_distance = get_allowable_distance(radius)

    candidate_nodes = filter_nodes(nodes, jd, fr, app_location, allowable_distance)
    print(candidate_nodes)
    node = max(candidate_nodes, key = candidate_nodes.get)
    print(node)
    return node



def scheduler(name, node, namespace="default"):
    print(node)
    vbtarget=client.V1ObjectReference(kind="Node", api_version="v1", name=node)
    
    meta=client.V1ObjectMeta(namespace=namespace, name=name)
    body=client.V1Binding(metadata=meta, target=vbtarget)

    return api.create_namespaced_binding(namespace, body, _preload_content=False)

def main():
    w = watch.Watch()
    for event in w.stream(api.list_namespaced_pod, "default"):
        pod = event['object']
        if pod.status.phase == "Pending":
            try:
                res = scheduler(pod.metadata.name, filter_sort(pod))
            except client.rest.ApiException as e:
                print(json.loads(e.body)['message'])
        
if __name__ == '__main__':
    main()
