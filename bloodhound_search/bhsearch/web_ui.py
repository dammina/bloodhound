#!/usr/bin/env python
# -*- coding: UTF-8 -*-

#  Licensed to the Apache Software Foundation (ASF) under one
#  or more contributor license agreements.  See the NOTICE file
#  distributed with this work for additional information
#  regarding copyright ownership.  The ASF licenses this file
#  to you under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance
#  with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.

r"""Bloodhound Search user interface."""
import copy
from collections import defaultdict

import pkg_resources
from bhsearch import BHSEARCH_CONFIG_SECTION
import re

from trac.core import Component, implements, TracError
from genshi.builder import tag
from genshi import HTML
from trac.perm import IPermissionRequestor
from trac.search import shorten_result
from trac.config import OrderedExtensionsOption, ListOption, Option, BoolOption
from trac.util.presentation import Paginator
from trac.util.datefmt import format_datetime, user_time
from trac.web import IRequestHandler, IRequestFilter
from trac.util.translation import _
from trac.util.html import find_element
from trac.web.chrome import (INavigationContributor, ITemplateProvider,
                             add_link, add_stylesheet, prevnext_nav,
                             web_context)
from bhsearch.api import (BloodhoundSearchApi, ISearchParticipant, SCORE, ASC,
                          DESC, IndexFields, SortInstruction)
from trac.wiki.formatter import extract_link

SEARCH_PERMISSION = 'SEARCH_VIEW'
DEFAULT_RESULTS_PER_PAGE = 10
SEARCH_URL = '/search'
BHSEARCH_URL = '/bhsearch'
SEARCH_URLS_RE = re.compile(r'/(?P<prefix>bh)?search(?P<suffix>.*)')

class RequestParameters(object):
    """
    Helper class for parameter parsing and creation of bhsearch specific URLs.

    Lifecycle of the class must be per request
    """
    QUERY = "q"
    PAGE = "page"
    TYPE = "type"
    NO_QUICK_JUMP = "noquickjump"
    PAGELEN = "pagelen"
    FILTER_QUERY = "fq"
    VIEW = "view"
    SORT = "sort"
    DEBUG = "debug"

    def __init__(self, req):
        self.req = req

        self.original_query = req.args.getfirst(self.QUERY)
        if self.original_query is None:
            self.query = ""
        else:
            self.query = self.original_query.strip()

        self.no_quick_jump = int(req.args.getfirst(self.NO_QUICK_JUMP, '0'))

        self.view = req.args.getfirst(self.VIEW)
        self.filter_queries = req.args.getlist(self.FILTER_QUERY)
        self.filter_queries = self._remove_possible_duplications(
            self.filter_queries)

        self.sort_string = req.args.getfirst(self.SORT)
        self.sort = self._parse_sort(self.sort_string)

        self.pagelen = int(req.args.getfirst(
            RequestParameters.PAGELEN,
            DEFAULT_RESULTS_PER_PAGE))
        self.page = int(req.args.getfirst(self.PAGE, '1'))
        self.type = req.args.getfirst(self.TYPE)
        self.debug = int(req.args.getfirst(self.DEBUG, '0'))

        self.params = {
            RequestParameters.FILTER_QUERY: []
        }

        if self.original_query is not None:
            self.params[self.QUERY] = self.original_query
        if self.no_quick_jump > 0:
            self.params[self.NO_QUICK_JUMP] = self.no_quick_jump
        if self.view is not None:
            self.params[self.VIEW] = self.view
        if self.pagelen != DEFAULT_RESULTS_PER_PAGE:
            self.params[self.PAGELEN] = self.pagelen
        if self.page > 1:
            self.params[self.PAGE] = self.page
        if self.type:
            self.params[self.TYPE] = self.type
        if self.filter_queries:
            self.params[RequestParameters.FILTER_QUERY] = self.filter_queries
        if self.sort_string:
            self.params[RequestParameters.SORT] = self.sort_string
        if self.debug:
            self.params[RequestParameters.DEBUG] = self.debug

    def _parse_sort(self, sort_string):
        if not sort_string:
            return None
        sort_terms = sort_string.split(",")
        sort = []

        for term in sort_terms:
            term = term.strip()
            if not term:
                continue
            term_parts = term.split()
            parts_count = len(term_parts)
            if parts_count == 1:
                sort.append(SortInstruction(term_parts[0], ASC))
            elif parts_count == 2:
                sort.append(SortInstruction(term_parts[0], term_parts[1]))
            else:
                raise TracError("Invalid sort term %s " % term)

        return sort if sort else None

    def _remove_possible_duplications(self, parameters_list):
        seen = set()
        return [parameter for parameter in parameters_list
                if parameter not in seen and not seen.add(parameter)]

    def create_href(
            self,
            page=None,
            type=None,
            skip_type=False,
            skip_page=False,
            additional_filter=None,
            force_filters=None,
            view=None,
            skip_view=False,
            sort=None,
            skip_sort = False,
            skip_query = False
    ):
        params = copy.deepcopy(self.params)

        if skip_sort:
            self._delete_if_exists(params, self.SORT)
        elif sort:
            params[self.SORT] = self._create_sort_expression(sort)

        #noquickjump parameter should be always set to 1 for urls
        params[self.NO_QUICK_JUMP] = 1

        if skip_view:
            self._delete_if_exists(params, self.VIEW)
        elif view:
            params[self.VIEW] = view

        if skip_page:
            self._delete_if_exists(params, self.PAGE)
        elif page:
            params[self.PAGE] = page

        if skip_type:
            self._delete_if_exists(params, self.TYPE)
        elif type:
            params[self.TYPE] = type

        if additional_filter and\
           additional_filter not in params[self.FILTER_QUERY]:
            params[self.FILTER_QUERY].append(additional_filter)
        elif force_filters is not None:
            params[self.FILTER_QUERY] = force_filters

        if skip_query:
            self._delete_if_exists(params, self.QUERY)

        return self.req.href.bhsearch(**params)

    def _create_sort_expression(self, sort):
        """
        Accepts single sort instruction e.g. SortInstruction(field, ASC) or
        list of sort instructions e.g.
        [SortInstruction(field1, ASC), SortInstruction(field2, DESC)]
        """
        if not sort:
            return None

        if isinstance(sort, SortInstruction):
            return sort.build_sort_expression()

        return ", ".join([item.build_sort_expression() for item in sort])


    def _delete_if_exists(self, params, name):
        if name in params:
            del params[name]


class BloodhoundSearchModule(Component):
    """Main search page"""
    implements(INavigationContributor, IPermissionRequestor, IRequestHandler,
        ITemplateProvider, IRequestFilter
        #           IWikiSyntaxProvider #todo: implement later
    )

    search_participants = OrderedExtensionsOption(
        'bhsearch',
        'search_participants',
        ISearchParticipant,
        "TicketSearchParticipant, WikiSearchParticipant"
    )

    prefix = "all"
    default_grid_fields = [
        IndexFields.ID,
        IndexFields.TYPE,
        IndexFields.TIME,
        IndexFields.AUTHOR,
        IndexFields.CONTENT,
    ]

    default_facets = ListOption(
        BHSEARCH_CONFIG_SECTION,
        prefix + '_default_facets',
        default=",".join([IndexFields.TYPE]),
        doc="""Default facets applied to search view of all resources""")

    default_view = Option(
        BHSEARCH_CONFIG_SECTION,
        prefix + '_default_view',
        doc="""If true, show grid as default view for specific resource in
            Bloodhound Search results""")

    all_grid_fields = ListOption(
        BHSEARCH_CONFIG_SECTION,
        prefix + '_default_grid_fields',
        default=",".join(default_grid_fields),
        doc="""Default fields for grid view for specific resource""")

    default_search = BoolOption('bhsearch', 'is_default', False)

    redirect_enabled = BoolOption('bhsearch', 'enable_redirect',
                                  False)

    # INavigationContributor methods
    def get_active_navigation_item(self, req):
        # pylint: disable=unused-argument
        return 'bhsearch'

    def get_navigation_items(self, req):
        if SEARCH_PERMISSION in req.perm:
            yield ('mainnav', 'bhsearch',
                   tag.a(_('Bloodhound Search'), href=self.env.href.bhsearch())
                )

    # IPermissionRequestor methods
    def get_permission_actions(self):
        return [SEARCH_PERMISSION]

    # IRequestHandler methods
    def match_request(self, req):
        return re.match('^%s' % BHSEARCH_URL, req.path_info) is not None

    def process_request(self, req):
        req.perm.assert_permission(SEARCH_PERMISSION)

        if self._is_opensearch_request(req):
            return ('opensearch.xml', {},
                    'application/opensearchdescription+xml')

        request_context = RequestContext(
            self.env,
            req,
            self.search_participants,
            self.default_view,
            self.all_grid_fields,
            self.default_facets
        )

        if request_context.requires_redirect:
            req.redirect(request_context.parameters.create_href(), True)

        # compatibility with legacy search
        req.search_query = request_context.parameters.query

        query_result = BloodhoundSearchApi(self.env).query(
            request_context.parameters.query,
            pagenum=request_context.page,
            pagelen=request_context.pagelen,
            sort=request_context.sort,
            fields=request_context.fields,
            facets=request_context.facets,
            filter=request_context.query_filter,
            highlight=True,
            context=request_context,
        )

        request_context.process_results(query_result)
        return self._return_data(req, request_context.data)

    def _is_opensearch_request(self, req):
        return req.path_info == BHSEARCH_URL + '/opensearch'

    def _return_data(self, req, data):
        add_stylesheet(req, 'common/css/search.css')
        return 'bhsearch.html', data, None

    # ITemplateProvider methods
    def get_htdocs_dirs(self):
    #        return [('bhsearch',
    #                 pkg_resources.resource_filename(__name__, 'htdocs'))]
        return []

    def get_templates_dirs(self):
        return [pkg_resources.resource_filename(__name__, 'templates')]

    # IRequestFilter methods
    def pre_process_request(self, req, handler):
        if SEARCH_URLS_RE.match(req.path_info):
            if self.redirect_enabled:
                return self
        return handler

    def post_process_request(self, req, template, data, content_type):
        if data is not None:
            if self.redirect_enabled:
                data['search_handler'] = req.href.bhsearch()
            elif req.path_info.startswith(SEARCH_URL):
                data['search_handler'] = req.href.search()
            elif self.default_search or req.path_info.startswith(BHSEARCH_URL):
                data['search_handler'] = req.href.bhsearch()
            else:
                data['search_handler'] = req.href.search()
        return template, data, content_type


class RequestContext(object):
    DATA_ACTIVE_FILTER_QUERIES = 'active_filter_queries'
    DATA_ACTIVE_QUERY = 'active_query'
    DATA_BREADCRUMBS_TEMPLATE = 'resourcepath_template'
    DATA_HEADERS = "headers"
    DATA_ALL_VIEWS = "all_views"
    DATA_VIEW = "view"
    DATA_VIEW_GRID = "grid"
    DATA_FACET_COUNTS = 'facet_counts'
    DATA_DEBUG = 'debug'
    DATA_PAGE_HREF = 'page_href'
    DATA_RESULTS = 'results'
    DATA_QUERY = 'query'
    DATA_QUICK_JUMP = "quickjump"
    DATA_SEARCH_EXTRAS = 'extra_search_fields'

    #bhsearch may support more pluggable views later
    VIEWS_SUPPORTED = {
        None: "Free text",
        DATA_VIEW_GRID: "Grid"
    }

    VIEWS_WITH_KNOWN_FIELDS = [DATA_VIEW_GRID]
    OBLIGATORY_FIELDS_TO_SELECT = [IndexFields.ID, IndexFields.TYPE]
    DEFAULT_SORT = [SortInstruction(SCORE, ASC), SortInstruction("time", DESC)]

    def __init__(
            self,
            env,
            req,
            search_participants,
            default_view,
            all_grid_fields,
            default_facets,
            ):
        self.env = env
        self.req = req
        self.parameters = RequestParameters(req)
        self.data = {
            self.DATA_QUERY: self.parameters.query,
            self.DATA_SEARCH_EXTRAS: [],
        }
        self.search_participants = search_participants
        self.default_view = default_view
        self.all_grid_fields = all_grid_fields
        self.default_facets = default_facets
        self.view = None
        self.page = self.parameters.page
        self.pagelen = self.parameters.pagelen

        if self.parameters.sort:
            self.sort = self.parameters.sort
        else:
            self.sort = self.DEFAULT_SORT

        self.allowed_participants, self.sorted_participants = \
            self._get_allowed_participants(req)

        if self.parameters.type in self.allowed_participants:
            self.active_type = self.parameters.type
            self.active_participant = self.allowed_participants[
                                      self.active_type]
        else:
            self.active_type = None
            self.active_participant = None

        self._prepare_active_type()
        self._prepare_breadcrumb_items()
        self._prepare_hidden_search_fields()
        self._prepare_quick_jump()

        # Compatibility with trac search
        self._process_legacy_type_filters(req, search_participants)
        if req.path_info.startswith(SEARCH_URL):
            self.requires_redirect = True

        self.fields = self._prepare_fields_and_view()
        self.query_filter = self._prepare_query_filter()
        self.facets = self._prepare_facets()

    def _get_allowed_participants(self, req):
        allowed_participants = {}
        ordered_participants = []
        for participant in self.search_participants:
            if participant.is_allowed(req):
                allowed_participants[
                    participant.get_participant_type()] = participant
                ordered_participants.append(participant)
        return allowed_participants, ordered_participants

    def _prepare_active_type(self):
        active_type = self.parameters.type
        if active_type and active_type not in self.allowed_participants:
            raise TracError(_("Unsupported resource type: '%(name)s'",
                            name=active_type))

    def _prepare_breadcrumb_items(self):
        current_filters = self.parameters.filter_queries

        def remove_filter_from_list(filter_to_remove):
            new_filters = list(current_filters)
            new_filters.remove(filter_to_remove)
            return new_filters

        if self.active_type:
            type_query = self._create_term_expression('type', self.active_type)
            type_filters = [dict(
                href=self.parameters.create_href(skip_type=True,
                                                 force_filters=[]),
                label=unicode(self.active_type).capitalize(),
                query=type_query,
            )]
        else:
            type_filters = []

        active_filter_queries = [
            dict(
                href=self.parameters.create_href(
                    force_filters=remove_filter_from_list(filter_query)
                ),
                label=filter_query,
                query=filter_query,
            ) for filter_query in self.parameters.filter_queries
        ]
        active_query = dict(
            href=self.parameters.create_href(skip_query=True),
            label=u'"%s"' % self.parameters.query,
            query=self.parameters.query
        )

        self.data[self.DATA_ACTIVE_FILTER_QUERIES] = \
            type_filters + active_filter_queries
        self.data[self.DATA_ACTIVE_QUERY] = active_query

    def _prepare_hidden_search_fields(self):
        if self.active_type:
            self.data[self.DATA_SEARCH_EXTRAS].append(
                (RequestParameters.TYPE, self.active_type)
            )

        if self.parameters.view:
            self.data[self.DATA_SEARCH_EXTRAS].append(
                (RequestParameters.VIEW, self.parameters.view)
            )
        if self.parameters.sort:
            self.data[self.DATA_SEARCH_EXTRAS].append(
                (RequestParameters.SORT, self.parameters.sort_string)
            )
        for filter_query in self.parameters.filter_queries:
            self.data[self.DATA_SEARCH_EXTRAS].append(
                (RequestParameters.FILTER_QUERY, filter_query)
            )

    def _prepare_quick_jump(self):
        if not self.parameters.query:
            return
        check_result = self._check_quickjump(
            self.req,
            self.parameters.query)
        if check_result:
            self.data[self.DATA_QUICK_JUMP] = check_result

    #the method below is "copy/paste" from trac search/web_ui.py
    def _check_quickjump(self, req, kwd):
        """Look for search shortcuts"""
        # pylint: disable=maybe-no-member
        noquickjump = int(req.args.get('noquickjump', '0'))
        # Source quickjump   FIXME: delegate to ISearchSource.search_quickjump
        quickjump_href = None
        if kwd[0] == '/':
            quickjump_href = req.href.browser(kwd)
            name = kwd
            description = _('Browse repository path %(path)s', path=kwd)
        else:
            context = web_context(req, 'search')
            link = find_element(extract_link(self.env, context, kwd), 'href')
            if link is not None:
                quickjump_href = link.attrib.get('href')
                name = link.children
                description = link.attrib.get('title', '')
        if quickjump_href:
            # Only automatically redirect to local quickjump links
            if not quickjump_href.startswith(req.base_path or '/'):
                noquickjump = True
            if noquickjump:
                return {'href': quickjump_href, 'name': tag.EM(name),
                        'description': description}
            else:
                req.redirect(quickjump_href)

    def _prepare_fields_and_view(self):
        self._add_views_selector()
        self.view = self._get_view()
        if self.view:
            self.data[self.DATA_VIEW] = self.view
        fields_to_select = None
        if self.view in self.VIEWS_WITH_KNOWN_FIELDS:
            if self.active_participant:
                fields_in_view = self.active_participant.\
                    get_default_view_fields(self.view)
            elif self.view == self.DATA_VIEW_GRID:
                fields_in_view = self.all_grid_fields
            else:
                raise TracError("Unsupported view: %s" % self.view)
            self.data[self.DATA_HEADERS] = [self._create_headers_item(field)
                                        for field in fields_in_view]
            fields_to_select = self._add_obligatory_fields(
                fields_in_view)
        return fields_to_select

    def _add_views_selector(self):
        active_view = self.parameters.view

        all_views = []
        for view, label in self.VIEWS_SUPPORTED.iteritems():
            all_views.append(dict(
                label=_(label),
                href=self.parameters.create_href(
                    view=view, skip_view=(view is None)),
                is_active = (view == active_view)
            ))
        self.data[self.DATA_ALL_VIEWS] = all_views

    def _get_view(self):
        view = self.parameters.view
        if view is None:
            if self.active_participant:
                view = self.active_participant.get_default_view()
            else:
                view = self.default_view
        if view is not None:
            view =  view.strip().lower()
        if view == "":
            view = None
        return view

    def _add_obligatory_fields(self, fields_in_view):
        fields_to_select = list(fields_in_view)
        for obligatory_field in self.OBLIGATORY_FIELDS_TO_SELECT:
            if obligatory_field is not fields_to_select:
                fields_to_select.append(obligatory_field)
        return fields_to_select

    def _create_headers_item(self, field):
        current_sort_direction = self._get_current_sort_direction_for_field(
            field)
        href_sort_direction = DESC if current_sort_direction == ASC else ASC
        return dict(
            name=field,
            href=self.parameters.create_href(
                skip_page=True,
                sort=SortInstruction(field, href_sort_direction)
            ),
            #TODO:add translated column label. Now it is really temporary
            # workaround
            label=field,
            sort=current_sort_direction,
        )

    def _get_current_sort_direction_for_field(self, field):
        if self.sort and len(self.sort) == 1:
            single_sort = self.sort[0]
            if single_sort.field == field:
                return single_sort.order
        return None

    def _prepare_query_filter(self):
        query_filters = list(self.parameters.filter_queries)
        if self.active_type:
            query_filters.append(
                self._create_term_expression(
                    IndexFields.TYPE, self.active_type))
        return query_filters

    def _create_term_expression(self, field, field_value):
        if field_value is None:
            query = "NOT (%s:*)" % field
        elif isinstance(field_value, basestring):
            query = '%s:"%s"' % (field, field_value)
        else:
            query = '%s:%s' % (field, field_value)
        return query

    def _prepare_facets(self):
        #TODO: add possibility of specifying facets in query parameters
        if self.active_participant:
            facets = self.active_participant.get_default_facets()
        else:
            facets = self.default_facets
        return facets

    def _process_legacy_type_filters(self, req, search_participants):
        legacy_type_filters = [sp.get_participant_type()
                               for sp in search_participants
                               if sp.get_participant_type() in req.args]
        if legacy_type_filters:
            params = self.parameters.params
            if len(legacy_type_filters) == 1:
                self.parameters.type = params[RequestParameters.TYPE] = \
                    legacy_type_filters[0]
            else:
                filter_queries = self.parameters.filter_queries
                if params[RequestParameters.FILTER_QUERY] is not filter_queries:
                    params[RequestParameters.FILTER_QUERY] = filter_queries
                filter_queries.append(
                    'type:(%s)' % ' OR '.join(legacy_type_filters)
                )
            self.requires_redirect = True
        else:
            self.requires_redirect = False

    def _process_doc(self, doc):
        ui_doc = dict(doc)
        ui_doc["href"] = self.req.href(doc['type'], doc['id'])
        #todo: perform content adaptation here

        if doc['content']:
            ui_doc['content'] = shorten_result(doc['content'])

        if doc['time']:
            ui_doc['date'] = user_time(self.req, format_datetime, doc['time'])

        is_free_text_view = self.view is None
        if is_free_text_view:
            participant = self.allowed_participants[doc['type']]
            ui_doc['title'] = participant.format_search_results(doc)
        return ui_doc

    def _prepare_results(self, result_docs, hits):
        ui_docs = [self._process_doc(doc) for doc in result_docs]

        results = Paginator(
            ui_docs,
            self.page - 1,
            self.pagelen,
            hits)

        self._prepare_shown_pages(results)
        results.current_page = {'href': None,
                                'class': 'current',
                                'string': str(results.page + 1),
                                'title': None}

        parameters = self.parameters
        if results.has_next_page:
            next_href = parameters.create_href(page=parameters.page + 1)
            add_link(self.req, 'next', next_href, _('Next Page'))

        if results.has_previous_page:
            prev_href = parameters.create_href(page=parameters.page - 1)
            add_link(self.req, 'prev', prev_href, _('Previous Page'))

        self.data[self.DATA_RESULTS] = results
        prevnext_nav(self.req, _('Previous'), _('Next'))

    def _prepare_shown_pages(self, results):
        shown_pages = results.get_shown_pages(self.pagelen)
        page_data = []
        for shown_page in shown_pages:
            page_href = self.parameters.create_href(page=shown_page)
            page_data.append([page_href, None, str(shown_page),
                              'page ' + str(shown_page)])
        fields = ['href', 'class', 'string', 'title']
        results.shown_pages = [dict(zip(fields, p)) for p in page_data]

    def process_results(self, query_result):
        docs = self._prepare_docs(query_result.docs,
                                  query_result.highlighting)
        self._prepare_results(docs, query_result.hits)
        self._prepare_result_facet_counts(query_result.facets)
        self._prepare_breadcrumbs_template()
        self.data[self.DATA_DEBUG] = query_result.debug
        if self.parameters.debug:
            self.data[self.DATA_DEBUG]['enabled'] = True
            self.data[self.DATA_SEARCH_EXTRAS].append(('debug', '1'))
        self.data[self.DATA_PAGE_HREF] = self.parameters.create_href()

    def _prepare_result_facet_counts(self, result_facets):
        """
        Sample query_result.facets content returned by query
        {
           'component': {None:2},
           'milestone': {None:1, 'm1':1},
        }

        returned facet_count contains href parameters:
        {
           'component': {None: {'count':2, href:'...'},
           'milestone': {
                            None: {'count':1,, href:'...'},
                            'm1':{'count':1, href:'...'}
                        },
        }

        """
        facet_counts = dict()
        if result_facets:
            for field, facets_dict in result_facets.iteritems():
                per_field_dict = dict()
                for field_value, count in facets_dict.iteritems():
                    if field == IndexFields.TYPE:
                        href = self.parameters.create_href(
                            skip_page=True,
                            force_filters=[],
                            type=field_value)
                    else:
                        href = self.parameters.create_href(
                            skip_page=True,
                            additional_filter=self._create_term_expression(
                                field,
                                field_value)
                        )
                    per_field_dict[field_value] = dict(
                        count=count,
                        href=href
                    )
                facet_counts[_(field)] = per_field_dict

        self.data[self.DATA_FACET_COUNTS] = facet_counts

    def _prepare_docs(self, docs, highlights):
        new_docs = []
        for doc, highlight in zip(docs, highlights):
            doc = defaultdict(str, doc)
            for field in highlight.iterkeys():
                highlighted_field = 'hilited_%s' % field
                if highlight[field]:
                    fragment = self._create_genshi_fragment(highlight[field])
                    doc[highlighted_field] = fragment
                else:
                    doc[highlighted_field] = ''
            new_docs.append(doc)
        return new_docs

    def _create_genshi_fragment(self, html_fragment):
        return tag(HTML(html_fragment))

    def _prepare_breadcrumbs_template(self):
        self.data[self.DATA_BREADCRUMBS_TEMPLATE] = 'bhsearch_breadcrumbs.html'
