import math
import random
import threading
import time
from datetime import datetime
import logging

from kubernetes import client, config
from sgp4.api import Satrec, jday

from k8s import *
from latency import get_rtt
from utils import *

earth_radius = 6378.135
altitude = 550
theta = math.acos(earth_radius / (earth_radius + altitude))
satellites = read_tles("tles.txt")
altitude = 550 # km
elevation_angle = np.radians(25) # radians


config.load_kube_config()
api = client.CoreV1Api()

logger = logging.getLogger(__name__)
FORMAT = '%(asctime)s %(clientip)-15s %(user)-8s %(message)s'
logging.basicConfig(format=FORMAT)
# fill in the latitude, logitude and elevation for your ground station
# I'm using Oregon here.
ground_station = (38.875, -121.707056, 0)

def fetch_sat_id(node):
    sat_id = node.metadata.labels.get('sat_id1', None)
    if not sat_id:
        sat_id = node.metadata.labels['sat_id']

    return int(sat_id)
    

def compute_node_positions(nodes, t) -> dict:
    node_positions = {}
    for node in nodes:
        sat_id = fetch_sat_id(node)
        l1 = satellites[sat_id]['line1']
        l2 = satellites[sat_id]['line2']
        satellite = Satrec.twoline2rv(l1, l2)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
        e,location, velocity = satellite.sgp4(jd, fr)

        node_positions[node.metadata.name] = location

    return node_positions


def filter_sort(pods_to_reschedule, nodes_for_reschedule, jd_list, fr_list, app_locations, allowable_distances, random_node=False, closest_node=False):
    print("pods for updation", len(pods_to_reschedule))
    pods_to_check = []
    for i in range(len(pods_to_reschedule)):
        pod = pods_to_reschedule[i]
        nodes = nodes_for_reschedule[i]
        jd = jd_list[i]
        fr = fr_list[i]
        app_location = app_locations[i]
        allowable_distance = allowable_distances[i]
        # print(i, pod, nodes, jd,fr,app_location, allowable_distance)
        print("filter_sort", time.time())

        candidate_nodes = {}
        for node in nodes:
            if node.metadata.name == pod.spec.node_name:
                continue
            sat_id = fetch_sat_id(node)
            l1 = satellites[sat_id]['line1']
            l2 = satellites[sat_id]['line2']
            satellite = Satrec.twoline2rv(l1, l2)
            e, node_location, node_velocity = satellite.sgp4(jd, fr)
            distance = calculate_distance(node_location, app_location)
            print(distance)
            if(distance < allowable_distance):
                candidate_nodes[node.metadata.name] = (distance, node_velocity[0] * (app_location[0] - node_location[0]) 
                                                    + node_velocity[1] * (app_location[1] - node_location[1]) 
                                                    + node_velocity[2] * (app_location[2] - node_location[2]))

        print(candidate_nodes)
        
        if random_node:
            new_node = random.choice(list(candidate_nodes.keys()))
        elif closest_node:
            new_node = min(candidate_nodes.items(), key= lambda x: x[1][0])[0]
        else:
            new_node = max(candidate_nodes.items(), key = lambda x: x[1][1])[0]
        print(new_node)
        
        pod_name = pod.metadata.name
        if(pod_name.endswith(pod.spec.node_name)):
            pod_name = pod_name[0:len(pod_name) - 1 - len(pod.spec.node_name)]
        new_pod_name = pod_name + "-" + new_node

        
        new_pod = create_new_pod(api, pod, new_pod_name, new_node)
        pod_node = new_pod.spec.node_name
        print(new_pod.metadata.name)
        # time.sleep(5)
        pods_to_check.append((new_pod, pod))
        
    completed_pods = [False] * len(pods_to_check)
    while True:
        for i in range(len(pods_to_check)):
            if completed_pods[i]:
                continue
            new_pod = pods_to_check[i][0]
            pod = pods_to_check[i][1]
            print("checking status for ",new_pod.metadata.name, pod.metadata.name)
            if not is_pod_ready(api, new_pod):
                new_pod = get_pod(api, new_pod.metadata.name, new_pod.metadata.namespace)
                pods_to_check[i] = (new_pod, pod)
            else:
                delete_pod(api, pod)
                completed_pods[i] = True
                print("pod", i, "completed")
        
        if all(completed_pods):
            break
        time.sleep(1)
        # while(any(p.metadata.name == pod.metadata.name for p in get_pods(api))):
        #     #delete_pod(api,pod)
        #     time.sleep(5)

# Fetch the nodes in the zone of the pod
def get_zone_nodes(pod, probe_time):
    filtered_nodes = []
    nodes = get_follower_nodes(api)
    node_positions = compute_node_positions(nodes, datetime.fromtimestamp(probe_time))
    radius = pod.metadata.labels.get('radius', 100)
    app_location = parseLocation(pod.metadata.labels['leoregion'])
    app_x, app_y, app_z = geodetic2cartesian(app_location[0], app_location[1], altitude * 1000)
    allowable_distance = get_allowable_distance(radius, altitude, elevation_angle)
    for node in nodes:
        # if the pod is already on the node, it wouldn't be used after the handover
        if node.metadata.name == pod.spec.node_name:
            continue

        location = node_positions[node.metadata.name]
        distance = calculate_distance(location, (app_x, app_y, app_z))
        if distance < allowable_distance:
            filtered_nodes.append(node)

    return filtered_nodes

# get the best node amongst the filtered nodes.
def get_best_node(pod, probe_time, filtered_nodes, random_node=False, closest_node=False):
    if random_node:
        return random.choice(filtered_nodes)

    node_positions = compute_node_positions(filtered_nodes, datetime.fromtimestamp(probe_time))
    # radius = pod.metadata.labels.get('radius', 100)
    app_location = parseLocation(pod.metadata.labels['leoregion'])
    app_x, app_y, app_z = geodetic2cartesian(app_location[0], app_location[1], altitude * 1000)
    # allowable_distance = get_allowable_distance(radius, altitude, elevation_angle)

    distances = {}
    krios_metrics = {}
    for node in filtered_nodes:
        sat_id = fetch_sat_id(node)
        l1 = satellites[sat_id]['line1']
        l2 = satellites[sat_id]['line2']
        satellite = Satrec.twoline2rv(l1, l2)
        e, node_location, node_velocity = satellite.sgp4(jd, fr)
        distances[node] = calculate_distance(node_location, (app_x, app_y, app_z))
        krios_metrics[node] = node_velocity[0] * (app_location[0] - node_location[0]) 
                                            + node_velocity[1] * (app_location[1] - node_location[1]) 
                                            + node_velocity[2] * (app_location[2] - node_location[2])

    if closest_node:
        return min(distances.items(), key=lambda x: x[1])
    
    return max(krios_metrics.items(), key=lambda x: x[1])

# Identify when this satellite will on longer be accessible from the zone
def node_leaving_zone(curr_time: float, pod, node) -> float:
    last_visible_time = curr_time
    sat_id = fetch_sat_id(node)
    l1 = satellites[sat_id]['line1']
    l2 = satellites[sat_id]['line2']
    satellite = Satrec.twoline2rv(l1, l2)

    radius = pod.metadata.labels.get('radius', 100)
    app_location = parseLocation(pod.metadata.labels['leoregion'])
    app_x, app_y, app_z = geodetic2cartesian(app_location[0], app_location[1], altitude * 1000)
    allowable_distance = get_allowable_distance(radius, altitude, elevation_angle)

    probe_time = curr_time + 100
    out_of_bounds_time = curr_time + 1000
    while True:
        dt = datetime.fromtimestamp(probe_time)
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        e, location, velocity = satellite.sgp4(jd, fr)
        distance = calculate_distance(location, (app_x, app_y, app_z))
        if distance > allowable_distance:
            out_of_bounds_time = probe_time
            break

        last_visible_time = probe_time
        probe_time += 100

    # do binary search to find the exact time
    while out_of_bounds_time - last_visible_time > 1:
        probe_time = (out_of_bounds_time + last_visible_time) / 2
        dt = datetime.fromtimestamp(probe_time)
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        e, location, velocity = satellite.sgp4(jd, fr)
        distance = calculate_distance(location, (app_x, app_y, app_z))
        if distance > allowable_distance:
            out_of_bounds_time = probe_time
        else:
            last_visible_time = probe_time

    return last_visible_time

def handover_manager(pod, new_node):
    pod_name = pod.metadata.name
    if(pod_name.endswith(pod.spec.node_name)):
        pod_name = pod_name[0:len(pod_name) - 1 - len(pod.spec.node_name)]
    new_pod_name = pod_name + "-" + new_node.metadata.name

    
    new_pod = create_new_pod(api, pod, new_pod_name, new_node.metadata.name)
    pod_node = new_pod.spec.node_name
    print(new_pod.metadata.name)
    
    while not is_pod_ready(api, new_pod):
        new_pod = get_pod(api, new_pod.metadata.name, new_pod.metadata.namespace)
        time.sleep(1)

    delete_pod(api, pod)

# Sleep until the satellite is moving out of the zone, and then trigger the handover process
def pod_manager(pod, node, sleep_time, lookahead_time):
    time.sleep(sleep_time)
    filtered_nodes = get_zone_nodes(pod, lookahead_time)
    best_node = get_best_node(pod, lookahead_time, filtered_nodes)

    handover_manager(pod, best_node)

def controller_loop(lookahead=True, random_node=False, closest_node=False):
    active_pods = []

    tt = round(time.time())
    while True:
        # sleep until the next second
        if time.time() < tt:
            time.sleep(tt - time.time())

        
        logger.info("starting new loop")
        nodes = get_follower_nodes(api)
        node_positions = compute_node_positions(nodes, datetime.fromtimestamp(t))
        gs_x, gs_y, gs_z = geodetic2cartesian(ground_station[0], ground_station[1], 550000)
        logger.info("node positions computed")

        pods = get_pods(api)
        logger.info("pods fetched")
        if len(pods) == 0:
            tt += 1
            continue

        existing_pods = set()
        for pod in pods:
            if pod.name in existing_pods:
                continue

            if not is_pod_ready(api, pod):
                continue

            existing_pods.add(pod.name)
            pod_node = node for node in nodes if node.metadata.name == pod.spec.node_name
            if pod_node is None:
                logger.error("Pod %s is not on any node. It's node name is %s", pod.metadata.name, pod.spec.node_name)
                continue

            node_leaving_time = node_leaving_zone(tt, pod, pod_node, allowable_distance)
            logger.info("Node leaving time computed %f", node_leaving_time)

            if lookahead:
                just_ahead_time = 5 + 0.001 * get_rtt(calculate_distance((gs_x, gs_y, gs_z), node_positions[pod_node])) + (3000 / 7575) * (1 / (24 * 60 * 60))
            else:
                just_ahead_time = 0

            p = threading.Thread(pod_manager, args=(pod, pod_node, node_leaving_time - tt - just_ahead_time, just_ahead_time))
            p.start()


        # pod_node = None
        # pod_set = set()
        
        # pods_to_reschedule = []
        # nodes_for_reschedule = []
        # jd_list = []
        # fr_list = []
        # leoregions = []
        # allowable_distances = []

        # t3 = time.time()
        # ready_pods = 0
        # for pod in pods:
        #     if not is_pod_ready(api, pod):
        #         continue
        #     ready_pods = ready_pods + 1
        #     pod_set.add(pod.metadata.name)
        #     radius = pod.metadata.labels.get('radius', 100)
        #     app_location = parseLocation(pod.metadata.labels['leoregion'])
        #     app_x, app_y, app_z = geodetic2cartesian(app_location[0], app_location[1], 550000)
        #     allowable_distance = get_allowable_distance(radius, altitude, elevation_angle)

        #     # 5 seconds for app initialization
        #     # The model has a 1-3 km error every day. Speed of the satellite is approx 7575 m/s
        #     # So, for our 5 second tick, error would be (3000 / 7575) * (5 / 24 * 60 * 60)
        #     pod_node = pod.spec.node_name
        #     if lookahead:
        #         just_ahead_time = 5 + 0.001 * get_rtt(calculate_distance((gs_x, gs_y, gs_z), node_positions[pod_node])) + (3000 / 7575) * (1 / (24 * 60 * 60))
        #     else:
        #         just_ahead_time = 0
        #     dt = datetime.fromtimestamp(tt + just_ahead_time)
        #     jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)

        #     current_node = [x for x in nodes if x.metadata.name == pod_node][0]
        #     sat_id = fetch_sat_id(current_node)
        #     l1 = satellites[sat_id]['line1']
        #     l2 = satellites[sat_id]['line2']
        #     satellite = Satrec.twoline2rv(l1, l2)
        #     e,location, velocity = satellite.sgp4(jd, fr)
            
        #     # print(sat_id, location, calculate_distance(location, (app_x, app_y, app_z)), allowable_distance)
        #     if(allowable_distance < calculate_distance(location, (app_x, app_y, app_z))):
        #         if pod.metadata.name not in active_pods:
        #             active_pods.append(pod.metadata.name)
        #             pods_to_reschedule.append(pod)
        #             nodes_for_reschedule.append(nodes)
        #             jd_list.append(jd)
        #             fr_list.append(fr)
        #             leoregions.append((app_x, app_y, app_z))
        #             allowable_distances.append(allowable_distance)
        #     # print("time for one app ",time.time() - t2)
        
        # logger.info("pods filtered")

        # if len(pods_to_reschedule) > 0:
        #     p = threading.Thread(target = filter_sort, args=(pods_to_reschedule, nodes_for_reschedule, jd_list, fr_list, leoregions, allowable_distances, random_node, closest_node))
        #     p.start()

        # total_time = time.time() - t
        # # f.write("----------------------------------------new_iteration----------------------------------------\n")
        # # f.write("all_cases " + str(ready_pods) + "," + str(time_all_apps) + "," + str(total_time) + "\n")
        # if ready_pods > 0:
        #     f.write(str(len(pods)) + "," + str(time_all_apps) + "," + str(total_time) + "\n")
        # # f.write("average time for one app " + str(np.mean(times_per_app)) + "\n")
        # # f.write("median time for one app " + str(np.median(times_per_app)) + "\n")
        # # f.write("time for all apps " + str(time_all_apps) + "\n")
        # # f.write("total time for one iteration " + str(total_time) + "\n")

        # # print("----------------------------------------new_iteration----------------------------------------\n")
        # # print("average time for one app " + str(np.mean(times_per_app)) + "\n")
        # # print("median time for one app " + str(np.median(times_per_app)) + "\n")
        # # print("time for all apps " + str(time_all_apps) + "\n")
        # # print("total time for one iteration " + str(total_time) + "\n")

        # new_active_pods = []
        # for pod in active_pods:
        #     if pod in pod_set:
        #         new_active_pods.append(pod)

        # active_pods = new_active_pods

        tt += 1      

def main():
    controller_loop(lookahead=True, random_node=False, closest_node=False)

if __name__ == '__main__':
    main()