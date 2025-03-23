#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:ts=4:sw=4:softtabstop=4:smarttab:expandtab

# For use with Topaz Scripts Version 2.6
# Python 3, September 2020


#@@CALIBRE_COMPAT_CODE@@

from utilities import SafeUnbuffered

import sys
import csv
import os
import getopt
from struct import pack, unpack

class TpzDRMError(Exception):
    pass

# Get a 7 bit encoded number from string. The most
# significant byte comes first and has the high bit (8th) set

def readEncodedNumber(file):
    flag = False
    c = file.read(1)
    if (len(c) == 0):
        return None
    data = ord(c)

    if data == 0xFF:
        flag = True
        c = file.read(1)
        if (len(c) == 0):
            return None
        data = ord(c)

    if data >= 0x80:
        datax = (data & 0x7F)
        while data >= 0x80 :
            c = file.read(1)
            if (len(c) == 0):
                return None
            data = c[0]
            datax = (datax <<7) + (data & 0x7F)
        data = datax

    if flag:
        data = -data
    return data


# returns a binary string that encodes a number into 7 bits
# most significant byte first which has the high bit set

def encodeNumber(number):
    result = ""
    negative = False
    flag = 0

    if number < 0 :
        number = -number + 1
        negative = True

    while True:
        byte = number & 0x7F
        number = number >> 7
        byte += flag
        result += chr(byte)
        flag = 0x80
        if number == 0 :
            if (byte == 0xFF and negative == False) :
                result += chr(0x80)
            break

    if negative:
        result += chr(0xFF)

    return result[::-1]



# create / read  a length prefixed string from the file

def lengthPrefixString(data):
    return encodeNumber(len(data))+data

def readString(file):
    stringLength = readEncodedNumber(file)
    if (stringLength == None):
        return ""
    sv = file.read(stringLength)
    if (len(sv)  != stringLength):
        return ""
    return unpack(str(stringLength)+"s",sv)[0]


# convert a binary string generated by encodeNumber (7 bit encoded number)
# to the value you would find inside the page*.dat files to be processed

def convert(i):
    result = ''
    val = encodeNumber(i)
    for j in range(len(val)):
        c = ord(val[j:j+1])
        result += '%02x' % c
    return result



# the complete string table used to store all book text content
# as well as the xml tokens and values that make sense out of it

class Dictionary(object):
    def __init__(self, dictFile):
        self.filename = dictFile
        self.size = 0
        self.fo = open(dictFile,'rb')
        self.stable = []
        self.size = readEncodedNumber(self.fo)
        for i in range(self.size):
            self.stable.append(self.escapestr(readString(self.fo)))
        self.pos = 0

    def escapestr(self, str):
        str = str.replace('&','&amp;')
        str = str.replace('<','&lt;')
        str = str.replace('>','&gt;')
        str = str.replace('=','&#61;')
        return str

    def lookup(self,val):
        if ((val >= 0) and (val < self.size)) :
            self.pos = val
            return self.stable[self.pos]
        else:
            print("Error - %d outside of string table limits" % val)
            raise TpzDRMError('outside of string table limits')
            # sys.exit(-1)

    def getSize(self):
        return self.size

    def getPos(self):
        return self.pos

    def dumpDict(self):
        for i in range(self.size):
            print("%d %s %s" % (i, convert(i), self.stable[i]))
        return

# parses the xml snippets that are represented by each page*.dat file.
# also parses the other0.dat file - the main stylesheet
# and information used to inject the xml snippets into page*.dat files

class PageParser(object):
    def __init__(self, filename, dict, debug, flat_xml):
        self.fo = open(filename,'rb')
        self.id = os.path.basename(filename).replace('.dat','')
        self.dict = dict
        self.debug = debug
        self.first_unknown = True
        self.flat_xml = flat_xml
        self.tagpath = []
        self.doc = []
        self.snippetList = []


    # hash table used to enable the decoding process
    # This has all been developed by trial and error so it may still have omissions or
    # contain errors
    # Format:
    # tag : (number of arguments, argument type, subtags present, special case of subtags presents when escaped)

    token_tags = {
        b'x'            : (1, 'scalar_number', 0, 0),
        b'y'            : (1, 'scalar_number', 0, 0),
        b'h'            : (1, 'scalar_number', 0, 0),
        b'w'            : (1, 'scalar_number', 0, 0),
        b'firstWord'    : (1, 'scalar_number', 0, 0),
        b'lastWord'     : (1, 'scalar_number', 0, 0),
        b'rootID'       : (1, 'scalar_number', 0, 0),
        b'stemID'       : (1, 'scalar_number', 0, 0),
        b'type'         : (1, 'scalar_text', 0, 0),

        b'info'            : (0, 'number', 1, 0),

        b'info.word'            : (0, 'number', 1, 1),
        b'info.word.ocrText'    : (1, 'text', 0, 0),
        b'info.word.firstGlyph' : (1, 'raw', 0, 0),
        b'info.word.lastGlyph'  : (1, 'raw', 0, 0),
        b'info.word.bl'         : (1, 'raw', 0, 0),
        b'info.word.link_id'    : (1, 'number', 0, 0),

        b'glyph'           : (0, 'number', 1, 1),
        b'glyph.x'         : (1, 'number', 0, 0),
        b'glyph.y'         : (1, 'number', 0, 0),
        b'glyph.glyphID'   : (1, 'number', 0, 0),

        b'dehyphen'          : (0, 'number', 1, 1),
        b'dehyphen.rootID'   : (1, 'number', 0, 0),
        b'dehyphen.stemID'   : (1, 'number', 0, 0),
        b'dehyphen.stemPage' : (1, 'number', 0, 0),
        b'dehyphen.sh'       : (1, 'number', 0, 0),

        b'links'        : (0, 'number', 1, 1),
        b'links.page'   : (1, 'number', 0, 0),
        b'links.rel'    : (1, 'number', 0, 0),
        b'links.row'    : (1, 'number', 0, 0),
        b'links.title'  : (1, 'text', 0, 0),
        b'links.href'   : (1, 'text', 0, 0),
        b'links.type'   : (1, 'text', 0, 0),
        b'links.id'     : (1, 'number', 0, 0),

        b'paraCont'          : (0, 'number', 1, 1),
        b'paraCont.rootID'   : (1, 'number', 0, 0),
        b'paraCont.stemID'   : (1, 'number', 0, 0),
        b'paraCont.stemPage' : (1, 'number', 0, 0),

        b'paraStems'        : (0, 'number', 1, 1),
        b'paraStems.stemID' : (1, 'number', 0, 0),

        b'wordStems'          : (0, 'number', 1, 1),
        b'wordStems.stemID'   : (1, 'number', 0, 0),

        b'empty'          : (1, 'snippets', 1, 0),

        b'page'           : (1, 'snippets', 1, 0),
        b'page.class'     : (1, 'scalar_text', 0, 0),
        b'page.pageid'    : (1, 'scalar_text', 0, 0),
        b'page.pagelabel' : (1, 'scalar_text', 0, 0),
        b'page.type'      : (1, 'scalar_text', 0, 0),
        b'page.h'         : (1, 'scalar_number', 0, 0),
        b'page.w'         : (1, 'scalar_number', 0, 0),
        b'page.startID' : (1, 'scalar_number', 0, 0),

        b'group'           : (1, 'snippets', 1, 0),
        b'group.class'     : (1, 'scalar_text', 0, 0),
        b'group.type'      : (1, 'scalar_text', 0, 0),
        b'group._tag'      : (1, 'scalar_text', 0, 0),
        b'group.orientation': (1, 'scalar_text', 0, 0),

        b'region'           : (1, 'snippets', 1, 0),
        b'region.class'     : (1, 'scalar_text', 0, 0),
        b'region.type'      : (1, 'scalar_text', 0, 0),
        b'region.x'         : (1, 'scalar_number', 0, 0),
        b'region.y'         : (1, 'scalar_number', 0, 0),
        b'region.h'         : (1, 'scalar_number', 0, 0),
        b'region.w'         : (1, 'scalar_number', 0, 0),
        b'region.orientation' : (1, 'scalar_text', 0, 0),

        b'empty_text_region' : (1, 'snippets', 1, 0),

        b'img'                   : (1, 'snippets', 1, 0),
        b'img.x'                 : (1, 'scalar_number', 0, 0),
        b'img.y'                 : (1, 'scalar_number', 0, 0),
        b'img.h'                 : (1, 'scalar_number', 0, 0),
        b'img.w'                 : (1, 'scalar_number', 0, 0),
        b'img.src'               : (1, 'scalar_number', 0, 0),
        b'img.color_src'         : (1, 'scalar_number', 0, 0),
        b'img.gridSize'          : (1, 'scalar_number', 0, 0),
        b'img.gridBottomCenter'  : (1, 'scalar_number', 0, 0),
        b'img.gridTopCenter'     : (1, 'scalar_number', 0, 0),
        b'img.gridBeginCenter'   : (1, 'scalar_number', 0, 0),
        b'img.gridEndCenter'     : (1, 'scalar_number', 0, 0),
        b'img.image_type'        : (1, 'scalar_number', 0, 0),

        b'paragraph'           : (1, 'snippets', 1, 0),
        b'paragraph.class'     : (1, 'scalar_text', 0, 0),
        b'paragraph.firstWord' : (1, 'scalar_number', 0, 0),
        b'paragraph.lastWord'  : (1, 'scalar_number', 0, 0),
        b'paragraph.lastWord'  : (1, 'scalar_number', 0, 0),
        b'paragraph.gridSize'  : (1, 'scalar_number', 0, 0),
        b'paragraph.gridBottomCenter'  : (1, 'scalar_number', 0, 0),
        b'paragraph.gridTopCenter'     : (1, 'scalar_number', 0, 0),
        b'paragraph.gridBeginCenter'   : (1, 'scalar_number', 0, 0),
        b'paragraph.gridEndCenter'     : (1, 'scalar_number', 0, 0),


        b'word_semantic'           : (1, 'snippets', 1, 1),
        b'word_semantic.type'      : (1, 'scalar_text', 0, 0),
        b'word_semantic.class'     : (1, 'scalar_text', 0, 0),
        b'word_semantic.firstWord' : (1, 'scalar_number', 0, 0),
        b'word_semantic.lastWord'  : (1, 'scalar_number', 0, 0),
        b'word_semantic.gridBottomCenter'  : (1, 'scalar_number', 0, 0),
        b'word_semantic.gridTopCenter'     : (1, 'scalar_number', 0, 0),
        b'word_semantic.gridBeginCenter'   : (1, 'scalar_number', 0, 0),
        b'word_semantic.gridEndCenter'     : (1, 'scalar_number', 0, 0),

        b'word'            : (1, 'snippets', 1, 0),
        b'word.type'       : (1, 'scalar_text', 0, 0),
        b'word.class'      : (1, 'scalar_text', 0, 0),
        b'word.firstGlyph' : (1, 'scalar_number', 0, 0),
        b'word.lastGlyph'  : (1, 'scalar_number', 0, 0),

        b'_span'           : (1, 'snippets', 1, 0),
        b'_span.class'     : (1, 'scalar_text', 0, 0),
        b'_span.firstWord' : (1, 'scalar_number', 0, 0),
        b'_span.lastWord'  : (1, 'scalar_number', 0, 0),
        b'_span.gridSize'  : (1, 'scalar_number', 0, 0),
        b'_span.gridBottomCenter'  : (1, 'scalar_number', 0, 0),
        b'_span.gridTopCenter' : (1, 'scalar_number', 0, 0),
        b'_span.gridBeginCenter' : (1, 'scalar_number', 0, 0),
        b'_span.gridEndCenter' : (1, 'scalar_number', 0, 0),

        b'span'           : (1, 'snippets', 1, 0),
        b'span.firstWord' : (1, 'scalar_number', 0, 0),
        b'span.lastWord'  : (1, 'scalar_number', 0, 0),
        b'span.gridSize'  : (1, 'scalar_number', 0, 0),
        b'span.gridBottomCenter'  : (1, 'scalar_number', 0, 0),
        b'span.gridTopCenter' : (1, 'scalar_number', 0, 0),
        b'span.gridBeginCenter' : (1, 'scalar_number', 0, 0),
        b'span.gridEndCenter' : (1, 'scalar_number', 0, 0),

        b'extratokens'                   : (1, 'snippets', 1, 0),
        b'extratokens.class'             : (1, 'scalar_text', 0, 0),
        b'extratokens.type'              : (1, 'scalar_text', 0, 0),
        b'extratokens.firstGlyph'        : (1, 'scalar_number', 0, 0),
        b'extratokens.lastGlyph'         : (1, 'scalar_number', 0, 0),
        b'extratokens.gridSize'          : (1, 'scalar_number', 0, 0),
        b'extratokens.gridBottomCenter'  : (1, 'scalar_number', 0, 0),
        b'extratokens.gridTopCenter'     : (1, 'scalar_number', 0, 0),
        b'extratokens.gridBeginCenter'   : (1, 'scalar_number', 0, 0),
        b'extratokens.gridEndCenter'     : (1, 'scalar_number', 0, 0),

        b'glyph.h'      : (1, 'number', 0, 0),
        b'glyph.w'      : (1, 'number', 0, 0),
        b'glyph.use'    : (1, 'number', 0, 0),
        b'glyph.vtx'    : (1, 'number', 0, 1),
        b'glyph.len'    : (1, 'number', 0, 1),
        b'glyph.dpi'    : (1, 'number', 0, 0),
        b'vtx'          : (0, 'number', 1, 1),
        b'vtx.x'        : (1, 'number', 0, 0),
        b'vtx.y'        : (1, 'number', 0, 0),
        b'len'          : (0, 'number', 1, 1),
        b'len.n'        : (1, 'number', 0, 0),

        b'book'         : (1, 'snippets', 1, 0),
        b'version'      : (1, 'snippets', 1, 0),
        b'version.FlowEdit_1_id'            : (1, 'scalar_text', 0, 0),
        b'version.FlowEdit_1_version'       : (1, 'scalar_text', 0, 0),
        b'version.Schema_id'                : (1, 'scalar_text', 0, 0),
        b'version.Schema_version'           : (1, 'scalar_text', 0, 0),
        b'version.Topaz_version'            : (1, 'scalar_text', 0, 0),
        b'version.WordDetailEdit_1_id'      : (1, 'scalar_text', 0, 0),
        b'version.WordDetailEdit_1_version' : (1, 'scalar_text', 0, 0),
        b'version.ZoneEdit_1_id'            : (1, 'scalar_text', 0, 0),
        b'version.ZoneEdit_1_version'       : (1, 'scalar_text', 0, 0),
        b'version.chapterheaders'           : (1, 'scalar_text', 0, 0),
        b'version.creation_date'            : (1, 'scalar_text', 0, 0),
        b'version.header_footer'            : (1, 'scalar_text', 0, 0),
        b'version.init_from_ocr'            : (1, 'scalar_text', 0, 0),
        b'version.letter_insertion'         : (1, 'scalar_text', 0, 0),
        b'version.xmlinj_convert'           : (1, 'scalar_text', 0, 0),
        b'version.xmlinj_reflow'            : (1, 'scalar_text', 0, 0),
        b'version.xmlinj_transform'         : (1, 'scalar_text', 0, 0),
        b'version.findlists'                : (1, 'scalar_text', 0, 0),
        b'version.page_num'                 : (1, 'scalar_text', 0, 0),
        b'version.page_type'                : (1, 'scalar_text', 0, 0),
        b'version.bad_text'                 : (1, 'scalar_text', 0, 0),
        b'version.glyph_mismatch'           : (1, 'scalar_text', 0, 0),
        b'version.margins'                  : (1, 'scalar_text', 0, 0),
        b'version.staggered_lines'          : (1, 'scalar_text', 0, 0),
        b'version.paragraph_continuation'   : (1, 'scalar_text', 0, 0),
        b'version.toc'                      : (1, 'scalar_text', 0, 0),

        b'stylesheet'                : (1, 'snippets', 1, 0),
        b'style'                     : (1, 'snippets', 1, 0),
        b'style._tag'                : (1, 'scalar_text', 0, 0),
        b'style.type'                : (1, 'scalar_text', 0, 0),
        b'style._after_type'         : (1, 'scalar_text', 0, 0),
        b'style._parent_type'        : (1, 'scalar_text', 0, 0),
        b'style._after_parent_type'  : (1, 'scalar_text', 0, 0),
        b'style.class'               : (1, 'scalar_text', 0, 0),
        b'style._after_class'        : (1, 'scalar_text', 0, 0),
        b'rule'                      : (1, 'snippets', 1, 0),
        b'rule.attr'                 : (1, 'scalar_text', 0, 0),
        b'rule.value'                : (1, 'scalar_text', 0, 0),

        b'original'      : (0, 'number', 1, 1),
        b'original.pnum' : (1, 'number', 0, 0),
        b'original.pid'  : (1, 'text', 0, 0),
        b'pages'        : (0, 'number', 1, 1),
        b'pages.ref'    : (1, 'number', 0, 0),
        b'pages.id'     : (1, 'number', 0, 0),
        b'startID'      : (0, 'number', 1, 1),
        b'startID.page' : (1, 'number', 0, 0),
        b'startID.id'   : (1, 'number', 0, 0),

        b'median_d'          : (1, 'number', 0, 0),
        b'median_h'          : (1, 'number', 0, 0),
        b'median_firsty'     : (1, 'number', 0, 0),
        b'median_lasty'      : (1, 'number', 0, 0),

        b'num_footers_maybe' : (1, 'number', 0, 0),
        b'num_footers_yes'   : (1, 'number', 0, 0),
        b'num_headers_maybe' : (1, 'number', 0, 0),
        b'num_headers_yes'   : (1, 'number', 0, 0),

        b'tracking'          : (1, 'number', 0, 0),
        b'src'               : (1, 'text', 0, 0),

     }


    # full tag path record keeping routines
    def tag_push(self, token):
        self.tagpath.append(token)
    def tag_pop(self):
        if len(self.tagpath) > 0 :
            self.tagpath.pop()
    def tagpath_len(self):
        return len(self.tagpath)
    def get_tagpath(self, i):
        cnt = len(self.tagpath)
        if i < cnt : result = self.tagpath[i]
        for j in range(i+1, cnt) :
            result += b'.' + self.tagpath[j]
        return result


    # list of absolute command byte values values that indicate
    # various types of loop meachanisms typically used to generate vectors

    cmd_list = (0x76, 0x76)

    # peek at and return 1 byte that is ahead by i bytes
    def peek(self, aheadi):
        c = self.fo.read(aheadi)
        if (len(c) == 0):
            return None
        self.fo.seek(-aheadi,1)
        c = c[-1:]
        return ord(c)


    # get the next value from the file being processed
    def getNext(self):
        nbyte = self.peek(1);
        if (nbyte == None):
            return None
        val = readEncodedNumber(self.fo)
        return val


    # format an arg by argtype
    def formatArg(self, arg, argtype):
        if (argtype == 'text') or (argtype == 'scalar_text') :
            result = self.dict.lookup(arg)
        elif (argtype == 'raw') or (argtype == 'number') or (argtype == 'scalar_number') :
            result = arg
        elif (argtype == 'snippets') :
            result = arg
        else :
            print("Error Unknown argtype %s" % argtype)
            sys.exit(-2)
        return result


    # process the next tag token, recursively handling subtags,
    # arguments, and commands
    def procToken(self, token):

        known_token = False
        self.tag_push(token)

        if self.debug : print('Processing: ', self.get_tagpath(0))
        cnt = self.tagpath_len()
        for j in range(cnt):
            tkn = self.get_tagpath(j)
            if tkn in self.token_tags :
                num_args = self.token_tags[tkn][0]
                argtype = self.token_tags[tkn][1]
                subtags = self.token_tags[tkn][2]
                splcase = self.token_tags[tkn][3]
                ntags = -1
                known_token = True
                break

        if known_token :

            # handle subtags if present
            subtagres = []
            if (splcase == 1):
                # this type of tag uses of escape marker 0x74 indicate subtag count
                if self.peek(1) == 0x74:
                    skip = readEncodedNumber(self.fo)
                    subtags = 1
                    num_args = 0

            if (subtags == 1):
                ntags = readEncodedNumber(self.fo)
                if self.debug : print('subtags: ', token , ' has ' , str(ntags))
                for j in range(ntags):
                    val = readEncodedNumber(self.fo)
                    subtagres.append(self.procToken(self.dict.lookup(val)))

            # arguments can be scalars or vectors of text or numbers
            argres = []
            if num_args > 0 :
                firstarg = self.peek(1)
                if (firstarg in self.cmd_list) and (argtype != 'scalar_number') and (argtype != 'scalar_text'):
                    # single argument is a variable length vector of data
                    arg = readEncodedNumber(self.fo)
                    argres = self.decodeCMD(arg,argtype)
                else :
                    # num_arg scalar arguments
                    for i in range(num_args):
                        argres.append(self.formatArg(readEncodedNumber(self.fo), argtype))

            # build the return tag
            result = []
            tkn = self.get_tagpath(0)
            result.append(tkn)
            result.append(subtagres)
            result.append(argtype)
            result.append(argres)
            self.tag_pop()
            return result

        # all tokens that need to be processed should be in the hash
        # table if it may indicate a problem, either new token
        # or an out of sync condition
        else:
            result = []
            if (self.debug or self.first_unknown):
                print('Unknown Token:', token)
                self.first_unknown = False
            self.tag_pop()
            return result


    # special loop used to process code snippets
    # it is NEVER used to format arguments.
    # builds the snippetList
    def doLoop72(self, argtype):
        cnt = readEncodedNumber(self.fo)
        if self.debug :
            result = 'Set of '+ str(cnt) + ' xml snippets. The overall structure \n'
            result += 'of the document is indicated by snippet number sets at the\n'
            result += 'end of each snippet. \n'
            print(result)
        for i in range(cnt):
            if self.debug: print('Snippet:',str(i))
            snippet = []
            snippet.append(i)
            val = readEncodedNumber(self.fo)
            snippet.append(self.procToken(self.dict.lookup(val)))
            self.snippetList.append(snippet)
        return



    # general loop code gracisouly submitted by "skindle" - thank you!
    def doLoop76Mode(self, argtype, cnt, mode):
        result = []
        adj = 0
        if mode & 1:
            adj = readEncodedNumber(self.fo)
        mode = mode >> 1
        x = []
        for i in range(cnt):
            x.append(readEncodedNumber(self.fo) - adj)
        for i in range(mode):
            for j in range(1, cnt):
                x[j] = x[j] + x[j - 1]
        for i in range(cnt):
            result.append(self.formatArg(x[i],argtype))
        return result


    # dispatches loop commands bytes with various modes
    # The 0x76 style loops are used to build vectors

    # This was all derived by trial and error and
    # new loop types may exist that are not handled here
    # since they did not appear in the test cases

    def decodeCMD(self, cmd, argtype):
        if (cmd == 0x76):

            # loop with cnt, and mode to control loop styles
            cnt = readEncodedNumber(self.fo)
            mode = readEncodedNumber(self.fo)

            if self.debug : print('Loop for', cnt, 'with  mode', mode,  ':  ')
            return self.doLoop76Mode(argtype, cnt, mode)

        if self.dbug: print("Unknown command", cmd)
        result = []
        return result



    # add full tag path to injected snippets
    def updateName(self, tag, prefix):
        name = tag[0]
        subtagList = tag[1]
        argtype = tag[2]
        argList = tag[3]
        nname = prefix + b'.' + name
        nsubtaglist = []
        for j in subtagList:
            nsubtaglist.append(self.updateName(j,prefix))
        ntag = []
        ntag.append(nname)
        ntag.append(nsubtaglist)
        ntag.append(argtype)
        ntag.append(argList)
        return ntag



    # perform depth first injection of specified snippets into this one
    def injectSnippets(self, snippet):
        snipno, tag = snippet
        name = tag[0]
        subtagList = tag[1]
        argtype = tag[2]
        argList = tag[3]
        nsubtagList = []
        if len(argList) > 0 :
            for j in argList:
                asnip = self.snippetList[j]
                aso, atag = self.injectSnippets(asnip)
                atag = self.updateName(atag, name)
                nsubtagList.append(atag)
        argtype='number'
        argList=[]
        if len(nsubtagList) > 0 :
            subtagList.extend(nsubtagList)
        tag = []
        tag.append(name)
        tag.append(subtagList)
        tag.append(argtype)
        tag.append(argList)
        snippet = []
        snippet.append(snipno)
        snippet.append(tag)
        return snippet



    # format the tag for output
    def formatTag(self, node):
        name = node[0]
        subtagList = node[1]
        argtype = node[2]
        argList = node[3]
        fullpathname = name.split(b'.')
        nodename = fullpathname.pop()
        ilvl = len(fullpathname)
        indent = b' ' * (3 * ilvl)
        rlst = []
        rlst.append(indent + b'<' + nodename + b'>')
        if len(argList) > 0:
            alst = []
            for j in argList:
                if (argtype == b'text') or (argtype == b'scalar_text') :
                    alst.append(j + b'|')
                else :
                    alst.append(str(j).encode('utf-8') + b',')
            argres = b"".join(alst)
            argres = argres[0:-1]
            if argtype == b'snippets' :
                rlst.append(b'snippets:' + argres)
            else :
                rlst.append(argres)
        if len(subtagList) > 0 :
            rlst.append(b'\n')
            for j in subtagList:
                if len(j) > 0 :
                    rlst.append(self.formatTag(j))
            rlst.append(indent + b'</' + nodename + b'>\n')
        else:
            rlst.append(b'</' + nodename + b'>\n')
        return b"".join(rlst)


    # flatten tag
    def flattenTag(self, node):
        name = node[0]
        subtagList = node[1]
        argtype = node[2]
        argList = node[3]
        rlst = []
        rlst.append(name)
        if (len(argList) > 0):
            alst = []
            for j in argList:
                if (argtype == 'text') or (argtype == 'scalar_text') :
                     alst.append(j + b'|')
                else :
                    alst.append(str(j).encode('utf-8') + b'|')
            argres = b"".join(alst)
            argres = argres[0:-1]
            if argtype == b'snippets' :
                rlst.append(b'.snippets=' + argres)
            else :
                rlst.append(b'=' + argres)
        rlst.append(b'\n')
        for j in subtagList:
            if len(j) > 0 :
                rlst.append(self.flattenTag(j))
        return b"".join(rlst)


    # reduce create xml output
    def formatDoc(self, flat_xml):
        rlst = []
        for j in self.doc :
            if len(j) > 0:
                if flat_xml:
                    rlst.append(self.flattenTag(j))
                else:
                    rlst.append(self.formatTag(j))
        result = b"".join(rlst)
        if self.debug : print(result)
        return result



    # main loop - parse the page.dat files
    # to create structured document and snippets

    # FIXME: value at end of magic appears to be a subtags count
    # but for what?  For now, inject an 'info" tag as it is in
    # every dictionary and seems close to what is meant
    # The alternative is to special case the last _ "0x5f" to mean something

    def process(self):

        # peek at the first bytes to see what type of file it is
        magic = self.fo.read(9)
        if (magic[0:1] == b'p') and (magic[2:9] == b'marker_'):
            first_token = b'info'
        elif (magic[0:1] == b'p') and (magic[2:9] == b'__PAGE_'):
            skip = self.fo.read(2)
            first_token = b'info'
        elif (magic[0:1] == b'p') and (magic[2:8] == b'_PAGE_'):
            first_token = b'info'
        elif (magic[0:1] == b'g') and (magic[2:9] == b'__GLYPH'):
            skip = self.fo.read(3)
            first_token = b'info'
        else :
            # other0.dat file
            first_token = None
            self.fo.seek(-9,1)


        # main loop to read and build the document tree
        while True:

            if first_token != None :
                # use "inserted" first token 'info' for page and glyph files
                tag = self.procToken(first_token)
                if len(tag) > 0 :
                    self.doc.append(tag)
                first_token = None

            v = self.getNext()
            if (v == None):
                break

            if (v == 0x72):
                self.doLoop72(b'number')
            elif (v > 0) and (v < self.dict.getSize()) :
                tag = self.procToken(self.dict.lookup(v))
                if len(tag) > 0 :
                    self.doc.append(tag)
            else:
                if self.debug:
                    print("Main Loop:  Unknown value: %x" % v)
                if (v == 0):
                    if (self.peek(1) == 0x5f):
                        skip = self.fo.read(1)
                        first_token = b'info'

        # now do snippet injection
        if len(self.snippetList) > 0 :
            if self.debug : print('Injecting Snippets:')
            snippet = self.injectSnippets(self.snippetList[0])
            snipno = snippet[0]
            tag_add = snippet[1]
            if self.debug : print(self.formatTag(tag_add))
            if len(tag_add) > 0:
                self.doc.append(tag_add)

        # handle generation of xml output
        xmlpage = self.formatDoc(self.flat_xml)

        return xmlpage


def fromData(dict, fname):
    flat_xml = True
    debug = True
    pp = PageParser(fname, dict, debug, flat_xml)
    xmlpage = pp.process()
    return xmlpage

def getXML(dict, fname):
    flat_xml = False
    debug = True
    pp = PageParser(fname, dict, debug, flat_xml)
    xmlpage = pp.process()
    return xmlpage

def usage():
    print('Usage: ')
    print('    convert2xml.py dict0000.dat infile.dat ')
    print(' ')
    print(' Options:')
    print('   -h            print this usage help message ')
    print('   -d            turn on debug output to check for potential errors ')
    print('   --flat-xml    output the flattened xml page description only ')
    print(' ')
    print('     This program will attempt to convert a page*.dat file or ')
    print(' glyphs*.dat file, using the dict0000.dat file, to its xml description. ')
    print(' ')
    print(' Use "cmbtc_dump.py" first to unencrypt, uncompress, and dump ')
    print(' the *.dat files from a Topaz format e-book.')

#
# Main
#

def main(argv):
    sys.stdout=SafeUnbuffered(sys.stdout)
    sys.stderr=SafeUnbuffered(sys.stderr)
    dictFile = ""
    pageFile = ""
    debug = True
    flat_xml = False
    printOutput = False
    if len(argv) == 0:
        printOutput = True
        argv = sys.argv

    try:
        opts, args = getopt.getopt(argv[1:], "hd", ["flat-xml"])

    except getopt.GetoptError as err:

        # print help information and exit:
        print(str(err)) # will print something like "option -a not recognized"
        usage()
        sys.exit(2)

    if len(opts) == 0 and len(args) == 0 :
        usage()
        sys.exit(2)

    for o, a in opts:
        if o =="-d":
            debug=True
        if o =="-h":
            usage()
            sys.exit(0)
        if o =="--flat-xml":
            flat_xml = True

    dictFile, pageFile = args[0], args[1]

    # read in the string table dictionary
    dict = Dictionary(dictFile)
    # dict.dumpDict()

    # create a page parser
    pp = PageParser(pageFile, dict, debug, flat_xml)

    xmlpage = pp.process()

    if printOutput:
        print(xmlpage)
        return 0

    return xmlpage

if __name__ == '__main__':
    sys.exit(main(''))
