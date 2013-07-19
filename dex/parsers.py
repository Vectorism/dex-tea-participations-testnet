__author__ = 'eric'

import re
import yaml
import yaml.constructor
from utils import pretty_json, small_json

try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict


################################################################################
# Query masking and scrubbing functions
################################################################################
def mask(parsed):
    return small_json(parsed)


def scrub(e):
    if isinstance(e, dict):
        return scrub_doc(e)
    elif isinstance(e, list):
        return scrub_list(e)
    else:
        return None


def scrub_doc(d):
    for k in d:
        d[k] = scrub(d[k])
        if d[k] is None:
            d[k] = "<val>"
    return d


def scrub_list(a):
    v = []
    for e in a:
        e = scrub(e)
        if e is not None:
            v.append(scrub(e))
    return sorted(v)


################################################################################
# Parser
#   Provides a parse function that passes input to a round of handlers.
################################################################################
class Parser(object):
    def __init__(self, handlers):
        self._line_handlers = handlers

    def parse(self, input):
        """Passes input to each QueryLineHandler in use"""
        query = None
        for handler in self._line_handlers:
            try:
                query = handler.handle(input)
            except Exception as e:
                query = None
            finally:
                if query is not None:
                    return query
        return None


################################################################################
# ProfileParser
#   Extracts queries from profile entries using a single ProfileEntryHandler
################################################################################
class ProfileParser(Parser):
    def __init__(self):
        """Declares the QueryLineHandlers to use"""
        super(ProfileParser, self).__init__([self.ProfileEntryHandler()])

    ############################################################################
    # Base ProfileEntryHandler class
    #   Knows how to yamlfy a logline query
    ############################################################################
    class ProfileEntryHandler:
        ########################################################################
        def handle(self, input):
            raw_query = OrderedDict({})
            if ((input is not None) and
                    (input.has_key('op'))):
                if input['op'] == 'insert':
                    return None
                elif input['op'] == 'query':
                    if input['query'].has_key('$query'):
                        raw_query['query'] = input['query']['$query']
                        if input['query'].has_key('$orderby'):
                            orderby = input['query']['$orderby']
                            raw_query['orderby'] = orderby
                    else:
                        raw_query['query'] = input['query']
                    raw_query['millis'] = input['millis']
                    raw_query['ns'] = input['ns']
                    return raw_query
                elif input['op'] == 'update':
                    raw_query['query'] = input['query']
                    if input.has_key('updateobj'):
                        if input['updateobj'].has_key('orderby'):
                            orderby = input['updateobj']['orderby']
                            raw_query['orderby'] = orderby
                    raw_query['millis'] = input['millis']
                    raw_query['ns'] = input['ns']
                    return raw_query
                elif ((input['op'] == 'command') and
                      (input['command'].has_key('count'))):
                    raw_query = { 'query': input['command']['query'] }
                    db = input['ns'][0:input['ns'].rfind('.')]
                    raw_query['millis'] = input['millis']
                    raw_query['ns'] = db + "." + input['command']['count']
                    return raw_query
            else:
                return None


################################################################################
# LogParser
#   Extracts queries from log lines using a list of QueryLineHandlers
################################################################################
class LogParser(Parser):
    def __init__(self):
        """Declares the QueryLineHandlers to use"""
        super(LogParser, self).__init__([StandardQueryHandler(),
                                         CmdQueryHandler(),
                                         UpdateQueryHandler()])


############################################################################
# Base QueryLineHandler class
#   Knows how to yamlfy a logline query
############################################################################
class QueryLineHandler:
    ########################################################################
    def parse_query(self, extracted_query):
        temp_query = yaml.load(extracted_query, OrderedDictYAMLLoader)

        if '$query' not in temp_query:
            return OrderedDict([('$query', temp_query)])
        else:
            return temp_query

    def parse_line_stats(self, stat_string):
        line_stats = {}
        split = stat_string.split(" ")

        for stat in split:
            if stat is not "" and stat is not None and stat is not "locks(micros)":
                stat_split = stat.split(":")
                if (stat_split is not None) and (stat_split is not "") and (len(stat_split) is 2):
                    try:
                        line_stats[stat_split[0]] = int(stat_split[1])
                    except:
                        pass

        return line_stats


############################################################################
# StandardQueryHandler
#   QueryLineHandler implementation for general queries (incl. getmore)
############################################################################
class StandardQueryHandler(QueryLineHandler):
    ########################################################################
    def __init__(self):
        self.name = 'Standard Query Log Line Handler'
        self._regex = '.*\[(?P<connection>\S*)\] '
        self._regex += '(?P<operation>\S+) (?P<ns>\S+\.\S+) query: '
        self._regex += '(?P<query>\{.*\}) (?P<stats>(\S+ )*)'
        self._regex += '(?P<query_time>\d+)ms'
        self._rx = re.compile(self._regex)

    ########################################################################
    def handle(self, input):
        match = self._rx.match(input)
        if match is not None:
            parsed = self.parse_query(match.group('query'))
            result = OrderedDict()
            if parsed is not None:
                scrubbed = scrub(parsed)
                result['query'] = scrubbed['$query']
                if '$orderby' in scrubbed:
                    result['orderby'] = scrubbed['$orderby']
                result['ns'] = match.group('ns')
                result['stats'] = self.parse_line_stats(match.group('stats'))
                result['stats']['millis'] = match.group('query_time')
                result['queryMask'] = mask(scrubbed)
                return result
        return None


############################################################################
# CmdQueryHandler
#   QueryLineHandler implementation for $cmd queries (count, findandmodify)
############################################################################
class CmdQueryHandler(QueryLineHandler):
    ########################################################################
    def __init__(self):
        self.name = 'CMD Log Line Handler'
        self._regex = '.*\[conn(?P<connection_id>\d+)\] '
        self._regex += 'command (?P<db>\S+)\.\$cmd command: '
        self._regex += '(?P<query>\{.*\}) (?P<stats>(\S+ )*)'
        self._regex += '(?P<query_time>\d+)ms'
        self._rx = re.compile(self._regex)

    ########################################################################
    def handle(self, input):
        match = self._rx.match(input)
        if match is not None:
            query = self.parse_query(match.group('query'))
            if query is not None:
                query['millis'] = match.group('query_time')
                if query.has_key('count'):
                    query['ns'] = match.group('db') + '.'
                    query['ns'] += query['count']
                elif query.has_key('findandmodify'):
                    if query.has_key('sort'):
                        query['orderby'] = query['sort']
                        del(query['sort'])
                    query['ns'] = match.group('db') + '.'
                    query['ns'] += query['findandmodify']
                else:
                    return None
                query['stats'] = self.parse_line_stats(match.group('stats'))
            return query
        return None


############################################################################
# UpdateQueryHandler
#   QueryLineHandler implementation for update queries
############################################################################
class UpdateQueryHandler(QueryLineHandler):
    ########################################################################
    def __init__(self):
        self.name = 'Update Log Line Handler'
        self._regex = '.*\[conn(?P<connection_id>\d+)\] '
        self._regex += 'update (?P<ns>\S+\.\S+) query: '
        self._regex += '(?P<query>\{.*\}) update: (?P<update>\{.*\}) '
        self._regex += '(?P<stats>(\S+ )*)(?P<query_time>\d+)ms'
        self._rx = re.compile(self._regex)

    ########################################################################
    def handle(self, input):
        match = self._rx.match(input)
        if match is not None:
            query = self.parse_query(match.group('query'))
            if query is not None:
                query['ns'] =  match.group('ns')
                query['millis'] = match.group('query_time')
                query['stats'] = self.parse_line_stats(match.group('stats'))
            return query
        return None


# From https://gist.github.com/844388
class OrderedDictYAMLLoader(yaml.Loader):
    """
    A YAML loader that loads mappings into ordered dictionaries.
    """

    def __init__(self, *args, **kwargs):
        yaml.Loader.__init__(self, *args, **kwargs)

        self.add_constructor(u'tag:yaml.org,2002:map', type(self).construct_yaml_map)
        self.add_constructor(u'tag:yaml.org,2002:omap', type(self).construct_yaml_map)

    def construct_yaml_map(self, node):
        data = OrderedDict()
        yield data
        value = self.construct_mapping(node)
        data.update(value)

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)
        else:
            raise yaml.constructor.ConstructorError(None, None,
                                                    'expected a mapping node, but found %s' % node.id, node.start_mark)

        mapping = OrderedDict()
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError, exc:
                raise yaml.constructor.ConstructorError('while constructing a mapping',
                                                        node.start_mark, 'found unacceptable key (%s)' % exc, key_node.start_mark)
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping



