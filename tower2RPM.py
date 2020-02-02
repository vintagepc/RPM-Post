#!/usr/bin/env python

import re
import sys
import math

def linspace(start, stop, n):
		if n == 1:
				yield stop
				return
		h = (stop - start) / (n - 1)
		for i in range(n):
				yield start + h * i

	

MAX_X = 250
K_VAL = 30	# NOTE: we extract user's filament K-val from gcode, but if it fails, sensible default.
FAN_TIME = 12 #seconds From RPM docs, 12 second cooling burst.
BUCKET_X = 253 # A sensible default, set "; BUCKET_X ###.# in your start gcode to configure.
BUCKET_OFFSET=10 # Position to move to before triggering the RPM.
PRINTER_MAX_VOLUMETRIC = 15 #mm^3/s. Use PS default, we min with the actual setting and filament setting later.
WIPING_OBJECTS = 0;

# pre-read to get print settings
settings = {}
object_purge = {}
current_tc = ""
current_in_purge = 0;
setting_re = re.compile(r'^\s*;\s*(\S+?)\s*=\s*(.*)$')
extruder_re = re.compile(r'E(\d+\.\d+)');
fp = open(sys.argv[1], 'r')
for line in fp:
	line = line.strip()
	match = setting_re.search(line)
	match_E = extruder_re.search(line)
	if "; toolchange #" in line: 
		if current_in_purge: # Did not find a purge finished in the last run, not infill.
			object_purge[current_tc] = 0
		current_tc = line.strip()
		object_purge[current_tc] = 0
	if current_tc and line.startswith("M900 K") and line.endswith("; Filament gcode"):
		current_in_purge = 1;
	if current_in_purge and match_E:
		object_purge[current_tc] += float(match_E.group(1))
	if "; PURGING FINISHED" in line:
		current_in_purge = 0
		WIPING_OBJECTS = 1
	if not match:
		continue
	
	settings[match.group(1)] = match.group(2)

fp.close()

#print("{}".format(object_purge))

if "single_extruder_multi_material" not in settings or \
	settings["single_extruder_multi_material"] != "1":
	print >> sys.stderr, "ERROR: Only single extruder MMU slices are supported."
	sys.exit(1)

#if "wipe_tower" not in settings or \
#	settings["wipe_tower"] != "1":
#	print >> sys.stderr, "ERROR: Only prints that include a wipe tower are supported."
#	sys.exit(1)

if "wipe_into_objects" in settings and \
	settings["wipe_into_objects"] == "1":
	print >> sys.stderr, "WARNING: Wiping into objects is not supported and may not work as expected/desired."
	WIPING_OBJECTS = 0;

if "wipe_into_infill" in settings and \
	settings["wipe_into_infill"] == "1":
	print >> sys.stderr, "WARNING: Wiping into infill is not supported and may not work as expected/desired."
	WIPING_OBJECTS = 0;

if "max_volumetric_speed" in settings:
	PRINTER_MAX_VOLUMETRIC = min(float(settings["max_volumetric_speed"]),PRINTER_MAX_VOLUMETRIC)

# Cheat to guess at the total number of tools available.
# This doesn't tell us what is used, just what exists. But it is also useful for user retract values.
retract_settings = settings["retract_length"].strip().split(",")
retract_tc_settings = settings["retract_length_toolchange"].strip().split(",")
retract_speed_settings = settings["retract_speed"].strip().split(",")
filament_volumetric_settings = settings["filament_max_volumetric_speed"].strip().split(",")
min_purge_settings =	settings["filament_minimal_purge_on_wipe_tower"].strip().split(",")
filament_cooling_moves_settings =	settings["filament_cooling_moves"].strip().split(",")
filament_cooling_initial_speed_settings =	settings["filament_cooling_initial_speed"].strip().split(",")
filament_cooling_final_speed_settings =	settings["filament_cooling_final_speed"].strip().split(",")
filament_unloading_speed_start_settings =	settings["filament_unloading_speed_start"].strip().split(",")
filament_unloading_speed_settings =	settings["filament_unloading_speed"].strip().split(",")
filament_loading_speed_start_settings =	settings["filament_loading_speed_start"].strip().split(",")
filament_loading_speed_end_settings =	settings["filament_loading_speed"].strip().split(",")
filament_ramming_settings =	settings["filament_ramming_parameters"].strip().split(";")
bottom_solid_layers = int(settings["bottom_solid_layers"].strip())
layer_height = float(settings["layer_height"].strip())
# We need this because of issue 2855 in PrusaSlicer...
LAST_SOLID_Z = bottom_solid_layers*layer_height;

total_tools = len(retract_settings)

printer = {
	"cooling_tube_pos": float(settings["cooling_tube_retraction"]),
	"cooling_tube_length": float(settings["cooling_tube_length"]),
	"filament_park_position": float(settings["parking_pos_retraction"]),
	"extra_loading_move": float(settings["extra_loading_move"])
	}


tools = []
for tool in range(total_tools):
	tools.append({
		"filament_diameter": 0.0,
		"purge": [],
		"retract" : float(retract_settings[tool]),
		"retract_tc" : float(retract_tc_settings[tool]),
		"retract_speed" : int(retract_speed_settings[tool])*60, # NOTE: *60 because F needs mm/min, PS gives mm/s
		"min_purge_vol" : float(min_purge_settings[tool]),
		"cooling_moves" : int(filament_cooling_moves_settings[tool]),
		"start_cool_speed" : float(filament_cooling_initial_speed_settings[tool])*60,
		"end_cool_speed" : float(filament_cooling_final_speed_settings[tool]) *60,
		"start_unload_speed" : float(filament_unloading_speed_start_settings[tool])*60,
		"end_unload_speed" : float(filament_unloading_speed_settings[tool]) *60,
		"start_load_speed" : float(filament_loading_speed_start_settings[tool])*60,
		"end_load_speed" : float(filament_loading_speed_end_settings[tool])*60,
		"max_vol_rate": min(float(filament_volumetric_settings[tool]),PRINTER_MAX_VOLUMETRIC), # So we can figure out fastest we can purge at.
		"ramming_parameters": []
	})
	tmp_ram = filament_ramming_settings[tool].strip().split("|")
	for val in tmp_ram[0].strip().split(" "):
		tools[tool]["ramming_parameters"].append(float(val.strip("\"")));
	del tools[tool]["ramming_parameters"][:2]; # First two are mostly useless here.


tool = 0
t = 0
for value in settings["wiping_volumes_matrix"].split(","):
	value = float(value)

	if t >= total_tools:
		t = 0
		tool += 1
	
	tools[tool]["purge"].append(value)
	t += 1

for setting in ["filament_diameter"]:
	tool = 0
	for value in settings[setting].split(","):		
		tools[tool][setting] = float(value)
		tool += 1

gcode = []
if WIPING_OBJECTS:
	gcode.append("Object/infill wiping detected!");
tower = {"type": None, "gcode": []}
skip = 0
last = {"X": 0.0, "Y": 0.0, "Z": 0.0, "K": "M900 K{}".format(K_VAL)}
done = False
fan_on = False
tc_id = 0;
printObject = {"type":None, "gcode":[]};


def purge_generate_RPM(length, maxrate):
	_rpm_gcode = [];
	_RPM_PURGE_SIZE = 40 # linear mm
	_RPM_CYCLES = int(math.ceil(length/_RPM_PURGE_SIZE));
	for i in range(0,_RPM_CYCLES): # Determine no. of purges we need to do
		_rpm_gcode.append("; Purge cycle {} of {}".format(i+1,_RPM_CYCLES))
		_rpm_gcode.append("G1 E{} F{:.1f}".format(_RPM_PURGE_SIZE,maxrate)) # These can't go much higher, else you start to skip/grind. Do the 40mm in one go.
		_rpm_gcode.append("M106") # Turn the fan on to cool the purge to keep the strand straight for fewer jams.
		_rpm_gcode.append("G4 S{:.0f}".format(FAN_TIME)) # TODO - split purge and do fan for part of it to speed things up?
		if not fan_on:
			_rpm_gcode.append("M107")# Turn the fan off again to resume the print
		_rpm_gcode.append("G1 X{0:.1f} F12000".format(BUCKET_X-BUCKET_OFFSET)) # Bump the bucket twice. 
		_rpm_gcode.append("G1 X{0:.1f} F3000".format(BUCKET_X))		
		_rpm_gcode.append("G1 X{0:.1f} F10000".format(BUCKET_X-BUCKET_OFFSET))
		if i != _RPM_CYCLES-1:
			_rpm_gcode.append("G1 X{0:.1f} F3000".format(BUCKET_X)) # Return to bucket for next purge cycle.
		_rpm_gcode.append("G4 S0; sync")			
	return _rpm_gcode;

fp = open(sys.argv[1], 'r')
for line in fp:
	line = line.strip()
	if done:
		gcode.append(line)
		continue

	if skip > 0:
		skip-=1
		continue
	
	if line.startswith("; BUCKET_X"):
		BUCKET_X = float(line[10:])
		MAX_X = BUCKET_X - BUCKET_OFFSET;
	
	if line.startswith("M106"):
		fan_on = True

	if line.startswith("M107"):
		fan_on = False
				
	if line.startswith("T") and "RPM FROM -1" in line:
		gcode.append(line)
		continue;
		
	# The K here is the filament custom gcode K value for linear advance. Typ. 30 but not guaranteed if the user has tuned it.
	if line.startswith("T") and "; RPM FROM" in line:
		last["T"] = int(line[-2:])
		tool = int(line[1:2])
		
		# Fixed settings at the start taken from example
		# PrusaSlicer 2.0.0 output
		gcode.append("; ------------------------")
		gcode.append("; BUCKET TOOL CHANGE START")
		if tc_id:
			gcode.append("; toolchange #{}".format(tc_id));
			
		tc_id+=1;
			
		gcode.append("M220 B")
		gcode.append("M220 S100")
		
		if last["Z"] < 40.0: # TODO: make bucket height configurable
			zMove = "Z40.0" # TODO: Make Z height and speed configurable
		else:
			zMove = ""

		# Move to edge fast, but push the trigger slowly
		# TODO: What is slowly and is it really needed?
		gcode.append("G1 X{:.3f} {} F10000".format(BUCKET_X-BUCKET_OFFSET,zMove)) # TODO: Make speed configurable
		gcode.append("G1 X{:.3f} F1000".format(BUCKET_X)) # TODO: Make trigger position configurable

	
		prev_tool =tools[last["T"]];
		# Cooling tube math:
		cooling_steps = linspace(prev_tool["start_cool_speed"],prev_tool["end_cool_speed"],2*prev_tool["cooling_moves"])
		cool_retract = -15 + printer["cooling_tube_pos"] + printer["cooling_tube_length"]/2;
		park_retract = printer["filament_park_position"] - printer["cooling_tube_length"]/2 - printer["cooling_tube_pos"];
		reload_distance = printer["filament_park_position"] + printer["extra_loading_move"]
		
		if fan_on:
			# Turn the fan off while we purge to the bucket
			gcode.append("M107")

					
		# NOTE: This is from the ramming parameters. They are volumetric rates in steps of 1/4 second. 
		ram_speeds = [];
		for ramrate in tools[last["T"]]["ramming_parameters"]:
			# Rate is mm^3/sec, for 1/4 second of it, calculate the linear distance.
			ram_len = (0.25*ramrate)/(math.pi * math.pow(0.5*tools[last["T"]]["filament_diameter"],2));
			# ESpeed is (length/time)*60 for linear mm/min. This will be lower than PS since we don't move X/Y.
			ram_speed_f = (ram_len/0.25)*60 
			gcode.append("G1 E{:.4f} F{:.0f} ; {:.4f} mm^3/sec for 0.25 sec".format(ram_len,ram_speed_f,ramrate))

		# This is the retract and cooling move stuff, the consts are from PS's wipetower code.
		gcode.append("G1 E-15.000 F{}".format(prev_tool["start_unload_speed"]))
		gcode.append("G1 E-{:.4f} F{}".format(cool_retract*0.7,prev_tool["end_unload_speed"]))
		gcode.append("G1 E-{:.4f} F{}".format(cool_retract*0.2,prev_tool["end_unload_speed"]*0.5))
		gcode.append("G1 E-{:.4f} F{}".format(cool_retract*0.1,prev_tool["end_unload_speed"]*0.3))
		gcode.append("; Cooling: {} to {} mm/s in {} moves".format(prev_tool["start_cool_speed"]/60,prev_tool["end_cool_speed"]/60,prev_tool["cooling_moves"]))
		# TODO: figure out the cooling speed calc, it doesn't agree with PS at the moment. Probably because Y/X movement.
		for move in cooling_steps:
			gcode.append("G1 E{:.3f} F{}".format(printer["cooling_tube_length"],move))
			gcode.append("G1 E-{:.3f} F{}".format(printer["cooling_tube_length"],next(cooling_steps)))
		gcode.append("G1 E-{:.4f} F2000".format(park_retract));
		gcode.append("G4 S0")

		purge = tools[last["T"]]["purge"][tool]
		diameter = tools[tool]["filament_diameter"]
		retract = tools[tool]["retract"]
		retract_speed = tools[tool]["retract_speed"]
		vol_rate = tools[tool]["max_vol_rate"]
		min_purge = tools[tool]["min_purge_vol"];
		
		# Calc max linear feedrate from volumetric:
		maxrate_mms = vol_rate/(math.pi*math.pow(0.5*diameter,2));
		maxrate = maxrate_mms*60.0;
		length = purge / (math.pi * math.pow(0.5*diameter, 2))
		min_length = min_purge / (math.pi * math.pow(0.5*diameter, 2))
		if WIPING_OBJECTS and last["tc_id"] in object_purge.keys():
			if last["Z"]<= LAST_SOLID_Z and object_purge[last["tc_id"]]>0:
				gcode.append("; BAD PURGE DETECTED - Plicer issue #2855. Ignoring.")
			else:
				gcode.append("; {} wipe: object holds {} mm".format(last["tc_id"], object_purge[last["tc_id"]]))
				bucket_purge = length - object_purge[last["tc_id"]];
				gcode.append("; {:.4f} mm in bucket (min {:.2f}), remainder in infill/object ".format(bucket_purge,min_length))
				bucket_purge = max(bucket_purge,min_length);
				length = bucket_purge
			

		gcode.append(line) # Reinsert toolchange/T-code
		# TODO: support non-similar temperatures/true multi-material?
		gcode.append("G4 S0")
		gcode.append("G1 E{:.4f} F{:.0f}".format(reload_distance*.2,tools[tool]["start_load_speed"])) # Prime to the nozzle. This can be reasonably fast.
		# HACK: the speed constant here doesn't agree with the PS code's outuput, probably because it's also moving in X
		gcode.append("G1 E{:.4f} F{:.0f}".format(reload_distance*.7,tools[tool]["end_load_speed"])) # Prime to the
		gcode.append("G1 E{:.4f} F{:.0f}".format(reload_distance*.1,tools[tool]["end_load_speed"]*0.1)) # Prime to the
		gcode.append("G4 S0; sync")
		gcode.append("; Purge generate for {:.2f} mm ({:.2f}mm^3) at F {:.0f}".format(length,purge,maxrate))
		# TODO - swap purge behaviour depending on mechanism.
		gcode += purge_generate_RPM(length, maxrate)
		gcode.append("G1 E-{:.4f} F{:.4f}; TC retract".format(tools[tool]["retract_tc"],tools[tool]["retract_speed"]))
		gcode.append("M220 R")
		gcode.append("G1 F6000") # context specific or always fixed?
		gcode.append("G4 S0")
		gcode.append("G92 E0")
		gcode.append("; BUCKET TOOL CHANGE END")
		gcode.append("; ----------------------")
		
		continue
	

	if "; Unload filament" in line:
		# We're all done, let everything else go through
		gcode.append("M107")
		gcode.append("G1 E-15.0000 F3000")
		gcode.append(line)
		done = True
		continue
	if line.startswith("G1 "):
		for bit in line.split(";")[0].split(" "):
			if len(bit)== 0 or bit == "":
				continue
			
			coord = bit[0]
			value = float(bit[1:])

			if coord not in ["X", "Y", "Z"]:
				continue
			last[coord] = value
			
	if last["X"] > MAX_X:
		print >> sys.stderr, "ERROR: Print extends into RPM activation area!"
		print >> sys.stderr, line
		sys.exit(1)
		# Skip everything until we get back in bounds
	gcode.append(line)

fp.close()

print ("\n".join(gcode))
