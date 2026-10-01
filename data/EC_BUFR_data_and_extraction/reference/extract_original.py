# (C) Copyright 2005- ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
#
# In applying this licence, ECMWF does not waive the privileges and immunities granted to it by
# virtue of its status as an intergovernmental organisation nor does it submit to any jurisdiction.
#
# Python implementation:  extract
#
# Description: how to read data of the ECMWF EPS tropical cyclone tracks encoded in BUFR format.
#
# Please note that tropical cyclone tracks can be encoded in various ways in BUFR.
# Therefore the code below might not work directly for other types of messages
# than the one used in the example. It is advised to use bufr_dump to
# understand the structure of the messages.
#
 
import collections
import os
import sys
import re
import datetime
import traceback
import argparse
 
from eccodes import *
 
VERBOSE = 1  ### verbose error reporting ###

missInt = -99  ### missing value ###
fc_range = 240  ### forecast range: 240 hours ###
fc_itvl = 6  ### forecast interval: 6 hours ###
fc_hr_num = int(fc_range / fc_itvl)  ### forecast number: 40 ###
det_mem_num = 1  ### deterministic member number: 1 ###
eps_mem_num = 51  ### ensemble member number: 51, 52 is for OBS data ###
english_number = ['ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN','EIGHT','NINE','TEN',
'ELEVEN','TWELVE','THIRTEEN','FOURTEEN','FIFTEEN','SIXTEEN','SEVENTEEN','EIGHTEEN','NINETEEN','TWENTY',
'TWENTYONE','TWENTYTWO','TWENTYTHRE','TWENTYFOUR','TWENTYFIVE','TWENTYSIX','TWENTYSEVE','TWENTYEIGH','TWENTYNINE','THIRTY',
'THIRTYONE','THIRTYTWO','THIRTYTHRE','THIRTYFOUR','THIRTYFIVE','THIRTYSIX','THIRTYSEVE','THIRTYEIGH','THIRTYNINE','FORTY',
'FORTYONE','FORTYTWO','FORTYTHREE','FORTYFOUR','FORTYFIVE','FORTYSIX','FORTYSEVEN','FORTYEIGHT','FORTYNINE','FIFTY',
'FIFTYONE','FIFTYTWO','FIFTYTHRE','FIFTYFOUR','FIFTYFIVE','FIFTYSIX','FIFTYSEVE','FIFTYEIGH','FIFTYNINE','SIXTY',
'SIXTYONE','SIXTYTWO','SIXTYTHRE','SIXTYFOUR','SIXTYFIVE','SIXTYSIX','SIXTYSEVE','SIXTYEIGH','SIXTYNINE']
data = collections.defaultdict(dict)  ### store decoded data ###

#################
### Functions ###
#################
##############################
### Function initiate data ###
##############################
def initData(mem_no):
    ### Fill in -99 of all members at each forecast hour ###
    for m in range(1, mem_no):
        for s in range(0, fc_hr_num + 1):
            fcHr = int(s*fc_itvl)
            data[m][s] = [fcHr, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt, missInt]

###########################
### Function check null ###
###########################
def checkNull(value):
    if value < -999:
        return missInt
    else:
        return value

##############################
### Function check integer ###
##############################
def is_integer(s):
	try:
		int(s)
		return True
	except ValueError:
		return False

###################################################
### Function export TXT                         ###
### yyyymmddhh_tcid_tcname_[DET/EPS].txt        ###
### yyyymmddhh_tcid_tcname_[DET/EPS]_radius.txt ###
###################################################
def exportTxt(output_data_path, basetime_str, tcID, tcName, file_type, data, mem_no):
    outfile_name = ('%s/%s_%s_%s_%s.txt' % (output_data_path, basetime_str, tcID, tcName, file_type))
    outfile_radius_name = ('%s/%s_%s_%s_%s_radius.txt' % (output_data_path, basetime_str, tcID, tcName, file_type))
    print('Write file %s' % outfile_name)
    print('Write file %s' % outfile_radius_name)

    outfile = open(outfile_name, 'w')
    outfile_radius = open(outfile_radius_name, 'w')

    outfile.write('%s\n' % basetime_str)
    outfile_radius.write('%s\n' % basetime_str)
    outfile.write('%s\n' % tcName)
    outfile_radius.write('%s\n' % tcName)
    outfile.write('%s\n' % tcID)
    outfile_radius.write('%s\n' % tcID)

    for m in range(1,mem_no):
        if (file_type == 'DET'):
            member = 'DET'
        elif (m == 51):
            member = 'M51 (CONTROL FCST)'
        else:
            member = 'M%02i'%(m)
        
        outfile.write('%s\n' % member)
        outfile_radius.write('%s\n' % member)

        for s in range(0, fc_hr_num + 1):
            outfile.write('{0:>03d}{1}{2:>10}{3}{4:>7.2f}{5}{6:>7.2f}{7}{8:>7.2f}{9}{10:>7.2f}\n'.format(
                data[m][s][0], ' ', data[m][s][1], ' ', data[m][s][2], ' ', data[m][s][3], ' ', data[m][s][4], ' ', data[m][s][5]))
            outfile_radius.write('{0:>03d}{1}{2:>10}{3}{4:>7.2f}{5}{6:>7.2f}{7}{8:>7.2f}{9}{10:>7.2f}{11}{12:>7.2f}{13}{14:>7.2f}{15}{16:>7.2f}{17}{18:>7.2f}{19}{20:>7.2f}{21}{22:>7.2f}{23}{24:>7.2f}{25}{26:>7.2f}{27}{28:>7.2f}{29}{30:>7.2f}{31}{32:>7.2f}{33}{34:>7.2f}\n'.format(
                data[m][s][0], ' ', data[m][s][1], ' ', data[m][s][2], ' ', data[m][s][3], ' ', data[m][s][4], ' ', data[m][s][5], ' ',
                data[m][s][6], ' ', data[m][s][7], ' ', data[m][s][8], ' ', data[m][s][9], ' ', data[m][s][10], ' ', data[m][s][11], ' ', 
                data[m][s][12], ' ', data[m][s][13], ' ', data[m][s][14], ' ', data[m][s][15], ' ', data[m][s][16], ' ', data[m][s][17]))

    ### Print OBS data for EPS file ###        
    if (file_type == 'EPS'):
        outfile.write('OBS\n')
        outfile_radius.write('OBS\n')

        ### For OBS, only write the T+0 data (extracted from DET.txt) ###
        det_file_name = re.sub('EPS', 'DET', outfile_name)
        det_radius_file_name = re.sub('EPS', 'DET', outfile_radius_name)
                    
        if (os.path.isfile(det_file_name)):
            det_file = open(det_file_name, 'r')
            content = det_file.readlines()
            for line in content:
                line_array = line.split()
                if (line_array[0] == '000'):
                    outfile.write(line)
                    break
            det_file.close()
        else:
            outfile.write('{0:>03d}{1}{2:>10}{3}{4:>7.2f}{5}{6:>7.2f}{7}{8:>7.2f}{9}{10:>7.2f}\n'.format(
                data[1][0][0], ' ', data[1][0][1], ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt))
            

        if (os.path.isfile(det_radius_file_name)):
            det_radius_file = open(det_radius_file_name, 'r')
            content = det_radius_file.readlines()
            for line in content:
                line_array = line.split()
                if (line_array[0] == '000'):
                    outfile_radius.write(line)
                    break
            det_radius_file.close()
        else:
            outfile_radius.write('{0:>03d}{1}{2:>10}{3}{4:>7.2f}{5}{6:>7.2f}{7}{8:>7.2f}{9}{10:>7.2f}{11}{12:>7.2f}{13}{14:>7.2f}{15}{16:>7.2f}{17}{18:>7.2f}{19}{20:>7.2f}{21}{22:>7.2f}{23}{24:>7.2f}{25}{26:>7.2f}{27}{28:>7.2f}{29}{30:>7.2f}{31}{32:>7.2f}{33}{34:>7.2f}\n'.format(
                data[1][0][0], ' ', data[1][0][1], ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ',
                missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', 
                missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt))
        
        ### For OBS, write -99 from 003 to 240 ###
        for s in range(1, fc_hr_num + 1): 
            outfile.write('{0:>03d}{1}{2:>10}{3}{4:>7.2f}{5}{6:>7.2f}{7}{8:>7.2f}{9}{10:>7.2f}\n'.format(
                missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt))
            outfile_radius.write('{0:>03d}{1}{2:>10}{3}{4:>7.2f}{5}{6:>7.2f}{7}{8:>7.2f}{9}{10:>7.2f}{11}{12:>7.2f}{13}{14:>7.2f}{15}{16:>7.2f}{17}{18:>7.2f}{19}{20:>7.2f}{21}{22:>7.2f}{23}{24:>7.2f}{25}{26:>7.2f}{27}{28:>7.2f}{29}{30:>7.2f}{31}{32:>7.2f}{33}{34:>7.2f}\n'.format(
                missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ',
                missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', 
                missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt, ' ', missInt))
        
    outfile.close()
    outfile_radius.close()

#################################################
### Function decode: get value from bufr file ###
#################################################
def decode(infile_name, output_data_path):
    print('Decoding %s' % infile_name)
    if (re.search('ECMF', infile_name) != None):
        file_type = 'DET'
        mem_no = det_mem_num
        detFlag = True
    elif (re.search('ECEP', infile_name) != None):
        file_type = 'EPS'
        mem_no = eps_mem_num
        detFlag = False
    else:
        print('Incorrect file type! Should be *ECMF* or *ECEP*')
        sys.exit(1)

    ### Initialize data ###
    initData(mem_no + 1)

    ### Open BUFR file ###
    f = open(infile_name, 'rb')
 
    cnt = 0

    ### loop for the messages in the file
    while 1:
        ### get handle for message ###
        bufr = codes_bufr_new_from_file(f)
        if bufr is None:
            break
 
        ### we need to instruct ecCodes to expand all the descriptors ###
        ### i.e. unpack the data values ###
        codes_set(bufr, 'unpack', 1)

        ### Get and check basetime ###
        year = codes_get(bufr, 'year')
        month = codes_get(bufr, 'month')
        day = codes_get(bufr, 'day')
        hour = codes_get(bufr, 'hour')
        if (year == '' or month == '' or day == '' or hour == ''):
            print ('Data error - no basetime in %s. Program exits.' % infile_name)
            sys.exit(1)

        basetime = datetime.datetime(year, month, day, hour)
        basetime_str = '%4i%02i%02i%02i' % (year, month, day, hour)

        ### Get and check storm identifier (TC ID) ###
        stormIdentifier = codes_get(bufr, 'stormIdentifier')
        #tcID = '%s%s' % (stormIdentifier[-1],stormIdentifier[0:-1].zfill(4))
        if (stormIdentifier == ''):
            print ('Data error - no storm identifier in %s. Program exits.' % infile_name)
            sys.exit(1)

        ### Get and check storm name (TC name) ###
        tcName = codes_get(bufr, 'longStormName').lstrip()
        if (tcName == ''):
            print ('Data error - no storm name in %s. Program exits.' % infile_name)
            sys.exit(1)

        ### Assign TC name with NAMELESS if it equals to TC ID ###
        if (tcName == stormIdentifier):
            tcName = 'NAMELESS'

        ### If the TC name is english number of TC ID, assign it with NAMELESS ###
        if (is_integer(stormIdentifier[0:2])):
            if (int(stormIdentifier[0:2]) < 70 and tcName == english_number[int(stormIdentifier[0:2])-1] ):
                tcName = 'NAMELESS'
        else:
            stormIdentifier = '%s%s' % ('00', stormIdentifier[2])

        tcID = '%s%04i'%(stormIdentifier[2], int(stormIdentifier[0:2]))

        ### How many different timePeriod in the data structure? ###
        numberOfPeriods = 0
        while True:
            numberOfPeriods = numberOfPeriods + 1
            try:
                codes_get_array(bufr, '#%d#timePeriod' % numberOfPeriods)
            except CodesInternalError as err:
                break
                ### the numberOfPeriods includes the analysis (period=0) ###
        
        timePeriod = [0 for x in range(numberOfPeriods)]
        fctime = [0 for x in range(numberOfPeriods)]
        fctime[0] = basetime_str
 
        ### Get ensembleMemberNumber ###
        memberNumber = codes_get_array(bufr, 'ensembleMemberNumber')
        memberNumberLen = len(memberNumber)
 
        latitudeAnalysis = codes_get_array(bufr, '#2#latitude')
        longitudeAnalysis = codes_get_array(bufr, '#2#longitude')

        ### For DET if 2nd latitude and 2nd longitude value are null in bufr, get the 1st latitude and 1st longitude instead
        if (detFlag and checkNull(latitudeAnalysis[0]) == missInt and checkNull(longitudeAnalysis[0]) == missInt):
            latitudeAnalysis = codes_get_array(bufr, '#1#latitude')
            longitudeAnalysis = codes_get_array(bufr, '#1#longitude')
            
        pressureAnalysis = codes_get_array(bufr, '#1#pressureReducedToMeanSeaLevel')
 
        ### Location of Maximum Wind ###
        significance = codes_get(bufr, '#3#meteorologicalAttributeSignificance')
 
        if significance != 3:
            print('ERROR: unexpected #3#meteorologicalAttributeSignificance=', significance)
            return 1
 
        latitudeMaxWind0 = codes_get_array(bufr, '#3#latitude')
        #longitudeMaxWind0 = codes_get_array(bufr, '#3#longitude')
        windMaxWind0 = codes_get_array(bufr, '#1#windSpeedAt10M')
        galeNE = codes_get_array(bufr, '#1#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        galeSE = codes_get_array(bufr, '#2#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        galeSW = codes_get_array(bufr, '#3#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        galeNW = codes_get_array(bufr, '#4#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        stormNE = codes_get_array(bufr, '#5#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        stormSE = codes_get_array(bufr, '#6#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        stormSW = codes_get_array(bufr, '#7#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        stormNW = codes_get_array(bufr, '#8#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        hurricaneNE = codes_get_array(bufr, '#9#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        hurricaneSE = codes_get_array(bufr, '#10#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        hurricaneSW = codes_get_array(bufr, '#11#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        hurricaneNW = codes_get_array(bufr, '#12#effectiveRadiusWithRespectToWindSpeedsAboveThreshold')
        if len(latitudeAnalysis) == len(memberNumber) and len(latitudeMaxWind0) == len(memberNumber):
            for k in range(memberNumberLen):
                galeNE_val = galeNE[0] if len(galeNE) == 1 else galeNE[k]
                galeSE_val = galeSE[0] if len(galeSE) == 1 else galeSE[k]
                galeSW_val = galeSW[0] if len(galeSW) == 1 else galeSW[k]
                galeNW_val = galeNW[0] if len(galeNW) == 1 else galeNW[k]
                stormNE_val = stormNE[0] if len(stormNE) == 1 else stormNE[k]
                stormSE_val = stormSE[0] if len(stormSE) == 1 else stormSE[k]
                stormSW_val = stormSW[0] if len(stormSW) == 1 else stormSW[k]
                stormNW_val = stormNW[0] if len(stormNW) == 1 else stormNW[k]
                hurricaneNE_val = hurricaneNE[0] if len(hurricaneNE) == 1 else hurricaneNE[k]
                hurricaneSE_val = hurricaneSE[0] if len(hurricaneSE) == 1 else hurricaneSE[k]
                hurricaneSW_val = hurricaneSW[0] if len(hurricaneSW) == 1 else hurricaneSW[k]
                hurricaneNW_val = hurricaneNW[0] if len(hurricaneNW) == 1 else hurricaneNW[k]
                if file_type == "DET":
                    data[1][0] = [timePeriod[0], fctime[0], checkNull(latitudeAnalysis[k]), checkNull(longitudeAnalysis[k]), checkNull(pressureAnalysis[k]/100), checkNull(windMaxWind0[k]), checkNull(galeNE_val/1000), checkNull(galeSE_val/1000), checkNull(galeSW_val/1000), checkNull(galeNW_val/1000), checkNull(stormNE_val/1000), checkNull(stormSE_val/1000), checkNull(stormSW_val/1000), checkNull(stormNW_val/1000), checkNull(hurricaneNE_val/1000), checkNull(hurricaneSE_val/1000), checkNull(hurricaneSW_val/1000), checkNull(hurricaneNW_val/1000)]
                else:
                    data[memberNumber[k]][0] = [timePeriod[0], fctime[0], checkNull(latitudeAnalysis[k]), checkNull(longitudeAnalysis[k]), checkNull(pressureAnalysis[k]/100), checkNull(windMaxWind0[k]), checkNull(galeNE_val/1000), checkNull(galeSE_val/1000), checkNull(galeSW_val/1000), checkNull(galeNW_val/1000), checkNull(stormNE_val/1000), checkNull(stormSE_val/1000), checkNull(stormSW_val/1000), checkNull(stormNW_val/1000), checkNull(hurricaneNE_val/1000), checkNull(hurricaneSE_val/1000), checkNull(hurricaneSW_val/1000), checkNull(hurricaneNW_val/1000)]
    
        else:
            for k in range(memberNumberLen):
                press_val = pressureAnalysis[0] if len(pressureAnalysis) == 1 else pressureAnalysis[k]
                wind_val = windMaxWind0[0] if len(windMaxWind0) == 1 else windMaxWind0[k]
                galeNE_val = galeNE[0] if len(galeNE) == 1 else galeNE[k]
                galeSE_val = galeSE[0] if len(galeSE) == 1 else galeSE[k]
                galeSW_val = galeSW[0] if len(galeSW) == 1 else galeSW[k]
                galeNW_val = galeNW[0] if len(galeNW) == 1 else galeNW[k]
                stormNE_val = stormNE[0] if len(stormNE) == 1 else stormNE[k]
                stormSE_val = stormSE[0] if len(stormSE) == 1 else stormSE[k]
                stormSW_val = stormSW[0] if len(stormSW) == 1 else stormSW[k]
                stormNW_val = stormNW[0] if len(stormNW) == 1 else stormNW[k]
                hurricaneNE_val = hurricaneNE[0] if len(hurricaneNE) == 1 else hurricaneNE[k]
                hurricaneSE_val = hurricaneSE[0] if len(hurricaneSE) == 1 else hurricaneSE[k]
                hurricaneSW_val = hurricaneSW[0] if len(hurricaneSW) == 1 else hurricaneSW[k]
                hurricaneNW_val = hurricaneNW[0] if len(hurricaneNW) == 1 else hurricaneNW[k]
                if file_type == "DET":
                    data[1][0] = [timePeriod[0], fctime[0], checkNull(latitudeAnalysis[0]), checkNull(longitudeAnalysis[0]), checkNull(press_val/100), checkNull(wind_val), checkNull(galeNE_val/1000), checkNull(galeSE_val/1000), checkNull(galeSW_val/1000), checkNull(galeNW_val/1000), checkNull(stormNE_val/1000), checkNull(stormSE_val/1000), checkNull(stormSW_val/1000), checkNull(stormNW_val/1000), checkNull(hurricaneNE_val/1000), checkNull(hurricaneSE_val/1000), checkNull(hurricaneSW_val/1000), checkNull(hurricaneNW_val/1000)]
                else:
                    data[memberNumber[k]][0] = [timePeriod[0], fctime[0], checkNull(latitudeAnalysis[0]), checkNull(longitudeAnalysis[0]), checkNull(press_val/100), checkNull(wind_val), checkNull(galeNE_val/1000), checkNull(galeSE_val/1000), checkNull(galeSW_val/1000), checkNull(galeNW_val/1000), checkNull(stormNE_val/1000), checkNull(stormSE_val/1000), checkNull(stormSW_val/1000), checkNull(stormNW_val/1000), checkNull(hurricaneNE_val/1000), checkNull(hurricaneSE_val/1000), checkNull(hurricaneSW_val/1000), checkNull(hurricaneNW_val/1000)]
 

        for i in range(1, numberOfPeriods):
            rank1 = i * 2 + 2
            rank3 = i * 2 + 3

            ivalues = codes_get_array(bufr, '#%d#timePeriod' % i)
 
            if len(ivalues) == 1:
                timePeriod[i] = ivalues[0]
                fctime[i] = '%s' % (basetime + datetime.timedelta(hours=int(ivalues[0]))).strftime('%Y%m%d%H')
            else:
                for j in range(len(ivalues)):
                    if ivalues[j] != CODES_MISSING_LONG:
                        timePeriod[i] = ivalues[j]
                        fctime[i] = '%s' % (basetime + datetime.timedelta(hours=int(ivalues[j]))).strftime('%Y%m%d%H')
                        break
 
            ### Location of the storm ###
            values = codes_get_array(bufr, '#%d#meteorologicalAttributeSignificance' % rank1)
            if len(values) == 1:
                significance = values[0]
            else:
                for j in range(len(values)):
                    if values[j] != CODES_MISSING_LONG:
                        significance = values[j]
                        break
 
            if significance == 1:
                lat = codes_get_array(bufr, '#%d#latitude' % rank1)
                lon = codes_get_array(bufr, '#%d#longitude' % rank1)
                press = codes_get_array(bufr, '#%d#pressureReducedToMeanSeaLevel' % (i + 1))
            else:
                print('ERROR: unexpected meteorologicalAttributeSignificance=', significance)
 
            ### Location of maximum wind ###
            values = codes_get_array(bufr, '#%d#meteorologicalAttributeSignificance' % rank3)
            if len(values) == 1:
                significanceWind = values[0]
            else:
                for j in range(len(values)):
                    if values[j] != CODES_MISSING_LONG:
                        significanceWind = values[j]
                        break
 
            if significanceWind == 3:
                #latWind = codes_get_array(bufr, '#%d#latitude' % rank3)
                #lonWind = codes_get_array(bufr, '#%d#longitude' % rank3)
                wind10m = codes_get_array(bufr, '#%d#windSpeedAt10M' % (i + 1))
                galeNE = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+1))
                galeSE = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+2))
                galeSW = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+3))
                galeNW = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+4))
                stormNE = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+5))
                stormSE = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+6))
                stormSW = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+7))
                stormNW = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+8))
                hurricaneNE = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+9))
                hurricaneSE = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+10))
                hurricaneSW = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+11))
                hurricaneNW = codes_get_array(bufr, '#%d#effectiveRadiusWithRespectToWindSpeedsAboveThreshold' % (i*12+12))
            else:
                print('ERROR: unexpected meteorologicalAttributeSignificance=', significanceWind)

            for k in range(memberNumberLen):
                lat_val = lat[0] if len(lat) == 1 else lat[k]
                lon_val = lon[0] if len(lon) == 1 else lon[k]
                press_val = press[0] if len(press) == 1 else press[k]
                wind10m_val = wind10m[0] if len(wind10m) == 1 else wind10m[k]
                galeNE_val = galeNE[0] if len(galeNE) == 1 else galeNE[k]
                galeSE_val = galeSE[0] if len(galeSE) == 1 else galeSE[k]
                galeSW_val = galeSW[0] if len(galeSW) == 1 else galeSW[k]
                galeNW_val = galeNW[0] if len(galeNW) == 1 else galeNW[k]
                stormNE_val = stormNE[0] if len(stormNE) == 1 else stormNE[k]
                stormSE_val = stormSE[0] if len(stormSE) == 1 else stormSE[k]
                stormSW_val = stormSW[0] if len(stormSW) == 1 else stormSW[k]
                stormNW_val = stormNW[0] if len(stormNW) == 1 else stormNW[k]
                hurricaneNE_val = hurricaneNE[0] if len(hurricaneNE) == 1 else hurricaneNE[k]
                hurricaneSE_val = hurricaneSE[0] if len(hurricaneSE) == 1 else hurricaneSE[k]
                hurricaneSW_val = hurricaneSW[0] if len(hurricaneSW) == 1 else hurricaneSW[k]
                hurricaneNW_val = hurricaneNW[0] if len(hurricaneNW) == 1 else hurricaneNW[k]
                if file_type == "DET":
                    data[1][i] = [timePeriod[i], fctime[i], checkNull(lat_val), checkNull(lon_val), checkNull(press_val/100), checkNull(wind10m_val), checkNull(galeNE_val/1000), checkNull(galeSE_val/1000), checkNull(galeSW_val/1000), checkNull(galeNW_val/1000), checkNull(stormNE_val/1000), checkNull(stormSE_val/1000), checkNull(stormSW_val/1000), checkNull(stormNW_val/1000), checkNull(hurricaneNE_val/1000), checkNull(hurricaneSE_val/1000), checkNull(hurricaneSW_val/1000), checkNull(hurricaneNW_val/1000)]
                else:
                    data[memberNumber[k]][i] = [timePeriod[i], fctime[i], checkNull(lat_val), checkNull(lon_val), checkNull(press_val/100), checkNull(wind10m_val), checkNull(galeNE_val/1000), checkNull(galeSE_val/1000), checkNull(galeSW_val/1000), checkNull(galeNW_val/1000), checkNull(stormNE_val/1000), checkNull(stormSE_val/1000), checkNull(stormSW_val/1000), checkNull(stormNW_val/1000), checkNull(hurricaneNE_val/1000), checkNull(hurricaneSE_val/1000), checkNull(hurricaneSW_val/1000), checkNull(hurricaneNW_val/1000)]
 
        ### Export TXT ###
        exportTxt(output_data_path, basetime_str, tcID, tcName, file_type, data, mem_no + 1)

        cnt += 1
 
        ### Release the BUFR message ###
        codes_release(bufr)
 
    ### Close the BUFR file ###
    f.close()

 
####################
### Main program ###
####################
def main():
    ### Initialize program settings ###
    ### Get input filename (.bufr4) and output folder path ###
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input_bufr', required=True, help='BUFR input file')
    parser.add_argument('-o', '--output_path', required=True, help='TXT output path')
    args = parser.parse_args()

    input_bufr = args.input_bufr
    output_path = args.output_path

    try:
        decode(input_bufr, output_path)
    except CodesInternalError as err:
        if VERBOSE:
            traceback.print_exc(file=sys.stderr)
        else:
            print(err.msg + '\n')
 
        return 1
    
    return 0
 
if __name__ == '__main__':
    sys.exit(main())
