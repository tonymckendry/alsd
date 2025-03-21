#!/usr/bin/python

# alsd -- Ableton Live set dumping utility
# by Andrew Bulhak  http://dev.null.org/acb/
#
# For instructions, read the README, or type alsd.py -h

import xml.etree.ElementTree as ET
import gzip
import plistlib

# the maybe monad
def bind(val, f):
    if val is None:
        return None
    return f(val)

hexchars = set('0123456789abcdef')
def decodeHexString(str):
    """Decode a hex string, ignoring all non-hex characters"""
    return filter(lambda c:c in hexchars, str.lower()).decode('hex')

def hideUnprintable(str, maskchar='.'):
    return ''.join(map(lambda c:(ord(c)>0x1f and ord(c)<0x7e) and c or maskchar, str))

def decodeTimeSignature(encoded):
    """Convert an Ableton Live integer-encoded time signature into a 
       (numerator,denominator) tuple"""
    return (encoded%99+1, 1<<(encoded/99))

#  ---- classes

# the boolean value type:
def BoolValue(str): return  (str == "true")

# return the type function for a string value
def guessTypeForValue(v):
    if v is None:
        return None
    if (v in ('true','false')): 
        return BoolValue
    for fn in (int, float):
        try:
            if fn(v) is not None: 
                return fn
        except ValueError:
            pass
    return None  # just a string

class ALSNode(object):
    """
    The parent class of all .als nodes
    """

    # the value fields we automatically extract from the element; each one 
    # has a name mapped to a type/conversion function; i.e., "Time" : float .

    valuefields = {}

    def __init__(self, elem):
        self.elem = elem
        # populate instance vars using harvested Value attributes from the element or subelements
        for (key, fspec) in self.valuefields.items():
            if type(fspec) == dict:
                (sel, ivar, vtype ) = (fspec.get('sel',key),fspec.get('ivar',key),fspec.get('type',None))
            elif type(fspec) == tuple:  # A simple (type, sel) tuple
                (sel, ivar, vtype) = (fspec[1], key, fspec[0])
            else:
                (sel, ivar, vtype) = (key, key[0].lower()+key[1:], fspec)
            val = self.valueForSubtag(sel)
            if val:
                if vtype:
                    try:
                        val = vtype(val)
                    except ValueError:
                        pass
            self.__dict__[ivar] = val


    def valueForSubtag(self, selector):
        return bind(self.elem.find(selector), lambda e: e.get("Value"))

    def valueForSubtagWithType(self, selector, type):
        if type is None: type = lambda x:x
        try:
            return type(self.valueForSubtag(selector))
        except ValueError:
            return None

    def intValueForSubtag(self, selector):
        return self.valueForSubtagWithType(selector, int)

    def floatValueForSubtag(self, selector):
        return self.valueForSubtagWithType(selector, float)

    def boolValueForSubtag(self, selector):
        return self.valueForSubtag(selector) == 'true'


# A node representing an automatable parameter

class ALSTrackMixerParam(ALSNode):
    def __init__(self, elem):
        super(ALSTrackMixerParam, self).__init__(elem)
        # determine the element type
        manual = self.valueForSubtag("Manual")
        self.type = guessTypeForValue(manual) 
        typefunc = self.type and self.type or (lambda x:x)
        self.manual = typefunc(manual)
        self.events = [ (int(e.get('Time')), typefunc(e.get('Value'))) for e in elem.findall("ArrangerAutomation/Events/*")]


class ALSTrackMixer(ALSNode):
    def __init__(self, elem):
        super(ALSTrackMixer, self).__init__(elem)
        self.params = dict([(e.tag, ALSTrackMixerParam(e)) for e in elem.findall("*[ArrangerAutomation]")])

# Clips and their component classes

class ALSWarpMarker(object):
    def __init__(self, elem):
        self.secTime = float(elem.get('SecTime'))
        self.beatTime = float(elem.get('BeatTime'))

class ALSMidiNote(object):
    def __init__(self, key, elem):
        # for some reason, Live stores MIDI velocities as floating-point values
        self.time, self.key, self.duration, self.velocity, self.offVelocity, self.isEnabled = (float(elem.get("Time")), key, float(elem.get("Duration")), float(elem.get("Velocity")), int(elem.get("OffVelocity")), 
            elem.get("IsEnabled")=="true")

class LiveSetMidiClipData(ALSNode):
    """
    An object encapsulating a MidiClip node.
    """
    valuefields = { 
      'Name': None, 'Annotation': None, 'LaunchMode':int, 'CurrentStart':float, 'CurrentEnd':float,  
      'loopStart' : (float, "Loop/LoopStart"), 'loopEnd' : (float, "Loop/LoopEnd"), 
      'loopStartRelative' : (float, "Loop/LoopStartRelative"), 
    }
    def __init__(self, elem):
        super(LiveSetMidiClipData, self).__init__(elem)
        self.warpmarkers = [ ALSWarpMarker(e) for e in elem.findall("WarpMarkers/WarpMarker")]
        self.length = (self.currentStart is not None and self.currentEnd is not None) and self.currentEnd-self.currentStart or None
        self.loopOn = self.boolValueForSubtag("Loop/LoopOn")
        self.loopLength = (self.loopStart is not None and self.loopEnd is not None) and self.loopEnd-self.loopStart or None

        self.notes = []
        for ktrk in elem.findall("Notes/KeyTracks/KeyTrack"):
            note = bind(ktrk.find("MidiKey"), lambda e:int(e.get("Value")))
            self.notes.extend([ALSMidiNote(note, mne) for mne in ktrk.findall("Notes/MidiNoteEvent")])
        self.notes.sort(key=lambda mn:mn.time)


# Devices 

class LiveSetAuPluginPresetData(object):
    """
    An object encapsulating the data stored in a preset buffer
    """
    def __init__(self, text):
        self.text = decodeHexString(text)
        self.plist = plistlib.readPlistFromString(self.text)
        self.name = self.plist.get('name')

class LiveSetDeviceData(ALSNode):
    """
    An object encapsulating the data for a device
    """
    def __init__(self, elem):
        super(LiveSetDeviceData, self).__init__(elem)
        self.deviceType = elem.tag
        self.auPresetBuffer = bind(elem.find("PluginDesc/AuPluginInfo/Preset/AuPreset/Buffer"), lambda e:LiveSetAuPluginPresetData(e.text))
        self.auPresetName = bind(self.auPresetBuffer, lambda b:b.name)

        self.presetName = self.valueForSubtag("UserName") \
                or bind(elem.find("PluginDesc/AuPluginInfo/Name"), lambda e:': '.join([v for v in [e.get("Value"),self.auPresetName] if v is not None])) \
                or self.valueForSubtag("PluginDesc/VstPluginInfo/PlugName") \
                or ""
        self.name = "%s: %s"%(self.deviceType, self.presetName)

# Tracks

class LiveSetTrackData(ALSNode):
    """
    An object encapsulating the data for a Track
    """
    valuefields = { 'Name' : None }
    def __init__(self, elem):
        super(LiveSetTrackData, self).__init__(elem)
        self.trackType = elem.tag
        
        # Handle device chain more safely
        device_chain = elem.find("DeviceChain")
        if device_chain is not None:
            devices_elem = device_chain.find("DeviceChain/Devices")
            self.devices = [LiveSetDeviceData(c) for c in devices_elem] if devices_elem is not None else []
        else:
            self.devices = []

        self.mixer = bind(elem.find("DeviceChain/Mixer"), ALSTrackMixer)

        # Handle clip slots more safely
        clip_slot_list = bind(elem.find("DeviceChain/MainSequencer/ClipSlotList"), lambda x:x)
        self.clipslots = clip_slot_list.findall("ClipSlot") if clip_slot_list is not None else []
        self.midiclips = [LiveSetMidiClipData(c) for c in elem.findall(".//MidiClip")] if elem is not None else []


class LiveSetData(object):
    """
    An object encapsulating a parsed Live set.
    """
    def __init__(self, path):
        self.etree = ET.parse(gzip.GzipFile(path))
        self.live_set = self.etree.getroot().find("LiveSet")
        self.tracks = [LiveSetTrackData(c) for c in self.live_set.find("Tracks")]
        self.mastertrack = bind(self.live_set.find("MasterTrack"), LiveSetTrackData)

    def timeSignatures(self):
        """Return an array of (beat time, (num,denom)) time signatures used in the track."""
        return [(max(t,0), decodeTimeSignature(enc)) for (t,enc) in self.mastertrack.mixer.params['TimeSignature'].events]


def dumpinfo(path, track=None, show_devices=True, show_clips=False, show_global=True):
    """Print out some info about an Ableton Live set at a path"""
    lsd = LiveSetData(path)

    if show_global and lsd.mastertrack is not None and lsd.mastertrack.mixer is not None:
        globalitems = lsd.mastertrack.mixer.params.items()
        globalitems.sort(key=lambda i:i[0])
        for pk, pv in globalitems:
            if pk != "TimeSignature":
                print("%s: "%pk, pv.manual)    
        timesigs =  lsd.timeSignatures()
        if len(timesigs) == 1:
            print("Time signature: %d/%d"%timesigs[0][1])
        else:
            print("Time signatures: %s"%(", ".join(["%d/%d"%ts for (tm,ts) in timesigs ])))

    def dumptrack(i, t):
        if t is None:
            return
        trackdesc = ', '.join([v for v in [ 
            t.trackType, 
            bind(t.mixer.params.get('Volume'), lambda v: "Vol %4.2f"%v.manual if v is not None and v.manual is not None else None) if t.mixer is not None else None,
            bind(t.mixer.params.get('Pan'), lambda v: "Pan %3.2f"%v.manual if v is not None and v.manual is not None else None) if t.mixer is not None else None,
        ] if v is not None])
        print("%d: %s (%s)"%(i, t.name or "<untitled>", trackdesc))
        if show_devices:
            for dev in t.devices:
                print("  %s"%dev.name)
        if show_clips:
            for clip in t.midiclips:
                print("""  Clip "%s" (loop length: %f bars)"""%(clip.name, clip.loopLength or 0)) #FIXME

    if track is not None:
        try:
            track = int(track)
        except ValueError:
            sys.exit("Track option must be a number")
        if track > len(lsd.tracks):
            sys.exit("%s has only %d tracks"%(path, len(lsd.tracks)))
        if track == 0 and lsd.mastertrack is not None:
            dumptrack(0, lsd.mastertrack)
        else:
            dumptrack(track, lsd.tracks[track-1])
    else:
        if lsd.mastertrack is not None:
            dumptrack(0, lsd.mastertrack)
        for (i,t) in enumerate(lsd.tracks):
            dumptrack(i+1, t)


if __name__ == "__main__":
    import sys
    import optparse

    globalopts = [
        optparse.make_option("-t", "--track", dest="track", help="The track number to display"),
        optparse.make_option("-D", "--show-devices", dest="show_devices", action="store_true", default=False, help="List devices for each track"),
        optparse.make_option("-C", "--show-clips", dest="show_clips", action="store_true", default=False, help="List clips for each track"),
        optparse.make_option("-M", "--show-mastertrack", dest="show_global", action="store_true", default=False, help="Display the mastertrack settings")
    ]

    optp = optparse.OptionParser(option_list=globalopts)
    (opts, args) = optp.parse_args(sys.argv[1:])
    for fn in args:
        dumpinfo(fn, track=opts.track, show_devices=opts.show_devices, show_clips=opts.show_clips, show_global=opts.show_global)

