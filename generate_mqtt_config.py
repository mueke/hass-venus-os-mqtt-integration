import re

import paho.mqtt.client as mqtt
import time
import yaml

JSON_VALUE_FLOAT_ROUND_2 = "{{ value_json.value | float | round(2) }}"


class ValueTemplate(str):
    pass

def literal_presenter(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

yaml.add_representer(ValueTemplate, literal_presenter)

import signal
import sys
import time
import yaml
import os
from dotenv import load_dotenv


mqttc = mqtt.Client()
mqttc.connect( os.getenv("MQTT_SERVER", "localhost"), int(os.getenv("MQTT_PORT","1883")), 60)
mqttc.subscribe(f"N/{os.getenv('VICTRON_ID')}/#", 0)

entity_id = lambda entity: entity.get('name').replace(" ", "_").lower()
collected_config = {}
changeable_select_fields = {}

class View:
    def __init__(self, title, entities):
        self.title = title
        self.path = title.replace(" ", "_").lower()
        self.theme = "Backend-selected"
        self.entities = entities
        self.cards = []

    def add_card(self, card):
        self.cards.append(card)

class Card:
    def __init__(self, title, columns=2):
        self.title = title
        self.columns = columns
        self.entities = []
        self.show_name = True
        self.show_state = True
        self.show_icon = True
        self.type = "glance"
        self.state_color = True

    def to_yaml(self):
        return yaml.dump(self, default_flow_style=False, indent=2)

    def addEntity(self, entity: str):
        self.entities.append(entity)


def gen_mqtt_config():
    sensors = list(collected_config.values())
    selects = list(changeable_select_fields.values())
    if len(sensors) > 0 or len(selects) > 0:
        mqtt = { "sensor" :  sensors,
                 "select" : selects
               }
        yaml_list = yaml.dump(mqtt, default_flow_style=False)
        print(yaml_list)
        with open('hass_mqtt.yaml', 'w+') as f:
            f.write(yaml_list)

def glance_card_config(collected_config_list):
    alarm_sensor_list = list(filter(lambda sensor: sensor.get('name').find('Alarm') > 0, collected_config_list))
    print(f"Alarm sensor list: {alarm_sensor_list}")
    card = {'show_name': True,
            'show_icon': True,
            'show_state': True,
            'type': "glance",
            'title': "Alarms",
            'columns': 5
            }

    card['entities']= [{"entity": f"sensor.{entity_id(c)}"} for c in alarm_sensor_list]
    print(f"Card: {card}")
    yaml_list = yaml.dump(card, default_flow_style=False,indent=2)
    print(yaml_list)
    with open('glance_card_config.yaml', 'w+') as f:
        f.write(yaml_list)

def changeable_config(changeable_select_fields):
    yaml_list = yaml.dump(changeable_select_fields, default_flow_style=False)
    print(yaml_list)
    with open('writeable_config.yaml', 'w+') as f:
        f.write(yaml_list)

def finish():
    mqttc.disconnect()
    config_list = list(collected_config.values())
    gen_mqtt_config()
    glance_card_config(config_list)

    sys.exit(0)

def signal_handler(signal, frame):
    print('You pressed Ctrl+C!')
    finish()

signal.signal(signal.SIGINT, signal_handler)

with open("attributes.csv", "r") as f:
    attr_lines = f.read().splitlines()

attr = {}
duplicates = {}
for line in attr_lines:

    splitted = line.split(",")
    if len(splitted) == 8:
        pkg,path,unit1,unit2,id,ctype,scale,readOnly = splitted
    else:
        pkg, path, unit1, unit2, id, ctype, scale, readOnly, comment = splitted
    key = f"{pkg.split('.')[-1]}{path}"
    if key in attr:
        print(f"Duplicate key: {key}")
        firstData = attr[key]
        secondData = {"package:":pkg,"path":path,"unit1": unit1, "unit2": unit2, "id": id, "ctype": ctype, "scale": scale, "readOnly": readOnly=="R", "writeable": readOnly=="W"}
        diff = [x for x in firstData if x in secondData and firstData[x] != secondData[x]]
        duplicates[key] = [firstData, secondData, diff]
    else:
        attr[key] = {"package:":pkg,"path":path,"unit1": unit1, "unit2": unit2, "id": id, "ctype": ctype, "scale": scale, "readOnly": readOnly=="R", "writeable": readOnly=="W"}
    #print("pkg: " + pkg + " path: " + path + " unit1: " + unit1 + " unit2: " + unit2 + " id: " + id + " ctype: " + ctype + " scale: " + scale + " readOnly: " + readOnly)
    if len(duplicates)>0:
        print(duplicates)
        #exit(1) if [x for x in duplicates if len(duplicates[x][2])>0] else print("No differences in duplicates")
print(len(attr.keys()))



def kv_if_template(k, v):
    return "{% if value_json.value == " + k + " %}" + v + "\n"

def kv_elif_template(k, v):
    return "{% elif value_json.value == " + k + " %}" + v + "\n"

def kv_else_end_template():
    return "{% else %} No Mapping for: { value_json.value }\n{% endif %}"

def value_if_template(k,v):
    return '{"value": {% if value == "'+v+'"%}' + k + "}\n"
def value_elif_template(k,v):
    return '{% elif value == "'+v+'"%}' + k + "}\n"
def value_else_end_template():
    return '{% endif %}'


def on_message(mosq, obj, msg):
    #print("Topic: " + msg.topic + "Message: " + str(msg.payload))
    global last_found

    print(f"Topic: {msg.topic}")
    topic = msg.topic.split("/")
    if len(topic) <= 3:
        return
    pkg = topic[2]
    deviceId = topic[3]
    path = '/'.join(topic[4:])
    key = f"{pkg}/{deviceId}/{path}"
    conf_key = f"{pkg}/{path}"
    conf = attr.get(conf_key)
    print(f"Pkg: {pkg}, DeviceId: {deviceId}, Path: {path}, Key: {key}, ConfKey: {conf_key}, Conf: {conf}, Payload: {msg.payload}")
    last_msg = time.time()
    if conf is not None and key not in collected_config and key not in changeable_select_fields :
        print( f"Adding {pkg}/{path}, Payload: {msg.payload}, Unit1: {conf['unit1']}, Unit2: {conf['unit2']}, Id: {conf['id']}, Ctype: {conf['ctype']}, Scale: {conf['scale']},ReadOnly: {conf['readOnly']}" )
        last_found = time.time()
        name = f'{str(pkg).capitalize()} {path.replace("/"," ")} {deviceId}'
        state_topic = f'venus-home/{msg.topic}'
        unique_id = f'{msg.topic.replace("/","_")}'
        sensor_data = {
            'state_topic': state_topic,
            'name': name,
            'unique_id': unique_id
        }
        is_lookup = str(conf["unit2"]).find(";") >= 0

        if is_lookup:
            kv_list = list(conf["unit2"].split(";"))
            first_k,first_v = kv_list.pop(0).split("=")
            lookup_template = kv_if_template(first_k,first_v)
            command_template = value_if_template(first_k,first_v)
            options = [first_v]
            for kv in kv_list:
                k, v = kv.split("=")
                print(f"K: {k} V: {v}")
                options.append(v)
                lookup_template += kv_elif_template(k,v)
                command_template += value_elif_template(k,v)
            lookup_template += kv_else_end_template()
            command_template += value_else_end_template()
            if conf.get('writeable'):
                changeable_select_fields[key] = {
                    "state_topic": state_topic,
                    "command_topic": state_topic.replace("/N/","/W/"),
                    "name": name,
                    "unique_id": unique_id,
                    "options": options,
                    "value_template": ValueTemplate(lookup_template),
                    "command_template": ValueTemplate(command_template),
                    "retain": False
                }
            else:
                sensor_data['value_template'] = ValueTemplate(lookup_template)
                collected_config[key] = sensor_data


        if conf.get('unit2') == "kWh":
            sensor_data['device_class'] = 'energy'
            sensor_data['unit_of_measurement'] =  "kWh"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'total_increasing'
            collected_config[key] = sensor_data

        if conf.get('unit2') == "W":
            sensor_data['device_class'] = 'power'
            sensor_data['unit_of_measurement'] =  "W"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'measurement'
            collected_config[key] = sensor_data

        if str(conf.get('unit2')).startswith("V"):
            sensor_data['device_class'] = 'voltage'
            sensor_data['unit_of_measurement'] =  "V"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'measurement'
            collected_config[key] = sensor_data

        if str(conf.get('unit2')).startswith("A"):
            sensor_data['device_class'] = 'current'
            sensor_data['unit_of_measurement'] =  "A"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'measurement'
            collected_config[key] = sensor_data

        if conf.get('unit2') == "Degrees celsius":
            sensor_data['device_class'] = 'temperature'
            sensor_data['unit_of_measurement'] =  "°C"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'measurement'
            collected_config[key] = sensor_data

        if conf.get('unit2') == "Hz":
            sensor_data['device_class'] = 'frequency'
            sensor_data['unit_of_measurement'] =  "Hz"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'measurement'
            collected_config[key] = sensor_data

        if conf.get('unit2') == "%":
            if re.search("Battery", path):
                sensor_data['device_class'] = 'battery'
            sensor_data['unit_of_measurement'] =  "%"
            sensor_data['value_template'] = JSON_VALUE_FLOAT_ROUND_2
            sensor_data['state_class'] = 'measurement'
            collected_config[key] = sensor_data

    if conf is None:
        print(f"Unknown conf key: {conf_key}")

    if 'last_found' in globals():
        last_found_sec = time.time() - last_found
        print(f"Collected {len(collected_config.keys())} sensors, {len(changeable_select_fields.keys())} selects, last found {last_found_sec} seconds ago, last message {time.time() - last_msg} seconds ago")
        if last_found_sec > 5:
            finish()

mqttc.on_message = on_message
mqttc.loop_forever()

