import math
from sgp4.exporter import export_tle
from sgp4.api import Satrec, WGS72
from sgp4.api import jday

earth_radius = 6378.135
altitude = 550
theta = math.acos(earth_radius / (earth_radius + altitude))

def read_tles(filename_tles):
    """
    Read a constellation of satellites from the TLES file.

    :param filename_tles:                    Filename of the TLES (typically /path/to/tles.txt)

    :return: Dictionary: {
                    "n_orbits":             Number of orbits
                    "n_sats_per_orbit":     Satellites per orbit
                    "epoch":                Epoch
                    "satellites":           Dictionary of satellite id to
                                            {"ephem_obj_manual": <obj>, "ephem_obj_direct": <obj>}
              }
    """
    satellites = []
    with open(filename_tles, 'r') as f:
        n_orbits, n_sats_per_orbit = [int(n) for n in f.readline().split()]
        universal_epoch = None
        i = 0
        for tles_line_1 in f:
            tles_line_2 = f.readline()
            tles_line_3 = f.readline()

            # Retrieve name and identifier
            name = tles_line_1
            sid = int(name.split()[1])
            if sid != i:
                raise ValueError("Satellite identifier is not increasing by one each line")
            i += 1

            satellite = {}
            satellite['line1'] = tles_line_2
            satellite['line2'] = tles_line_3

            satellites.append(satellite)

    return satellites

def geodetic2cartesian(lat_degrees, lon_degrees, ele_m):
    """
    Compute geodetic coordinates (latitude, longitude, elevation) to Cartesian coordinates.

    :param lat_degrees: Latitude in degrees (float)
    :param lon_degrees: Longitude in degrees (float)
    :param ele_m:  Elevation in meters

    :return: Cartesian coordinate as 3-tuple of (x, y, z)
    """

    #
    # Adapted from: https://github.com/andykee/pygeodesy/blob/master/pygeodesy/transform.py
    #

    # WGS72 value,
    # Source: https://geographiclib.sourceforge.io/html/NET/NETGeographicLib_8h_source.html
    a = 6378135.0

    # Ellipsoid flattening factor; WGS72 value
    # Taken from https://geographiclib.sourceforge.io/html/NET/NETGeographicLib_8h_source.html
    f = 1.0 / 298.26

    # First numerical eccentricity of ellipsoid
    e = math.sqrt(2.0 * f - f * f)
    lat = lat_degrees * (math.pi / 180.0)
    lon = lon_degrees * (math.pi / 180.0)

    # Radius of curvature in the prime vertical of the surface of the geodetic ellipsoid
    v = a / math.sqrt(1.0 - e * e * math.sin(lat) * math.sin(lat))

    x = (v + ele_m) * math.cos(lat) * math.cos(lon)
    y = (v + ele_m) * math.cos(lat) * math.sin(lon)
    z = (v * (1.0 - e * e) + ele_m) * math.sin(lat)

    return x / 1000, y / 1000, z / 1000

def parseLocation(location):
    lat,long = location.split('_')

    return float(lat), float(long), 0

def get_allowable_distance(app_radius):
    gamma = app_radius / earth_radius
    phi = theta - gamma
    return (earth_radius + altitude) * math.sin(phi)

def calculate_distance(point1, point2):
    return math.sqrt((point2[0] - point1[0]) * (point2[0] - point1[0]) 
        + (point2[1] - point1[1]) * (point2[1] - point1[1])
     + (point2[2] - point1[2]) * (point2[2] - point1[2]))