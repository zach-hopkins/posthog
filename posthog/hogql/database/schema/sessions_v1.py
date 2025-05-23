import re
from typing import cast, Optional, TYPE_CHECKING

from posthog.hogql import ast
from posthog.hogql.context import HogQLContext
from posthog.hogql.database.models import (
    StringDatabaseField,
    DateTimeDatabaseField,
    IntegerDatabaseField,
    Table,
    FieldOrTable,
    StringArrayDatabaseField,
    DatabaseField,
    LazyTable,
    FloatDatabaseField,
    BooleanDatabaseField,
    LazyTableToAdd,
    LazyJoinToAdd,
)
from posthog.hogql.database.schema.channel_type import (
    create_channel_type_expr,
    ChannelTypeExprs,
    DEFAULT_CHANNEL_TYPES,
)
from posthog.hogql.database.schema.util.where_clause_extractor import SessionMinTimestampWhereClauseExtractorV1
from posthog.hogql.errors import ResolutionError
from posthog.models.property_definition import PropertyType
from posthog.models.sessions.sql import (
    SELECT_SESSION_PROP_STRING_VALUES_SQL_WITH_FILTER,
    SELECT_SESSION_PROP_STRING_VALUES_SQL,
)
from posthog.queries.insight import insight_sync_execute
from posthog.schema import BounceRatePageViewMode

if TYPE_CHECKING:
    from posthog.models.team import Team

DEFAULT_BOUNCE_RATE_DURATION_SECONDS = 10

RAW_SESSIONS_FIELDS: dict[str, FieldOrTable] = {
    "id": StringDatabaseField(name="session_id", nullable=False),
    # TODO remove this, it's a duplicate of the correct session_id field below to get some trends working on a deadline
    "session_id": StringDatabaseField(name="session_id", nullable=False),
    "team_id": IntegerDatabaseField(name="team_id", nullable=False),
    "distinct_id": StringDatabaseField(name="distinct_id", nullable=False),
    "min_timestamp": DateTimeDatabaseField(name="min_timestamp", nullable=False),
    "max_timestamp": DateTimeDatabaseField(name="max_timestamp", nullable=False),
    # URLs / paths
    "urls": StringArrayDatabaseField(name="urls", nullable=False),
    # many of the fields in the raw tables are AggregateFunction state, rather than simple types
    "entry_url": DatabaseField(name="entry_url", nullable=False),
    "exit_url": DatabaseField(name="exit_url", nullable=False),
    "initial_referring_domain": DatabaseField(name="initial_referring_domain", nullable=False),
    # UTM parameters
    "initial_utm_source": DatabaseField(name="initial_utm_source", nullable=False),
    "initial_utm_campaign": DatabaseField(name="initial_utm_campaign", nullable=False),
    "initial_utm_medium": DatabaseField(name="initial_utm_medium", nullable=False),
    "initial_utm_term": DatabaseField(name="initial_utm_term", nullable=False),
    "initial_utm_content": DatabaseField(name="initial_utm_content", nullable=False),
    # Other Ad / campaign / attribution IDs
    "initial_gclid": DatabaseField(name="initial_gclid", nullable=False),
    "initial_gad_source": DatabaseField(name="initial_gad_source", nullable=False),
    "initial_gclsrc": DatabaseField(name="initial_gclsrc", nullable=False),
    "initial_dclid": DatabaseField(name="initial_dclid", nullable=False),
    "initial_gbraid": DatabaseField(name="initial_gbraid", nullable=False),
    "initial_wbraid": DatabaseField(name="initial_wbraid", nullable=False),
    "initial_fbclid": DatabaseField(name="initial_fbclid", nullable=False),
    "initial_msclkid": DatabaseField(name="initial_msclkid", nullable=False),
    "initial_twclid": DatabaseField(name="initial_twclid", nullable=False),
    "initial_li_fat_id": DatabaseField(name="initial_li_fat_id", nullable=False),
    "initial_mc_cid": DatabaseField(name="initial_mc_cid", nullable=False),
    "initial_igshid": DatabaseField(name="initial_igshid", nullable=False),
    "initial_ttclid": DatabaseField(name="initial_ttclid", nullable=False),
    # Counts (used in e.g. bounce rate)
    "event_count_map": DatabaseField(name="event_count_map", nullable=False),
    "pageview_count": IntegerDatabaseField(name="pageview_count", nullable=False),
    "autocapture_count": IntegerDatabaseField(name="autocapture_count", nullable=False),
}

LAZY_SESSIONS_FIELDS: dict[str, FieldOrTable] = {
    "id": StringDatabaseField(name="session_id", nullable=False),
    # TODO remove this, it's a duplicate of the correct session_id field below to get some trends working on a deadline
    "session_id": StringDatabaseField(name="session_id", nullable=False),
    "team_id": IntegerDatabaseField(name="team_id", nullable=False),
    "distinct_id": StringDatabaseField(name="distinct_id", nullable=False),
    "$start_timestamp": DateTimeDatabaseField(name="$start_timestamp", nullable=False),
    "$end_timestamp": DateTimeDatabaseField(name="$end_timestamp", nullable=False),
    # URLs / paths
    "$urls": StringArrayDatabaseField(name="$urls", nullable=False),
    "$num_uniq_urls": IntegerDatabaseField(name="$num_uniq_urls", nullable=False),
    "$entry_current_url": StringDatabaseField(name="$entry_current_url"),
    "$entry_pathname": StringDatabaseField(name="$entry_pathname"),
    "$entry_hostname": StringDatabaseField(name="$entry_host"),
    "$exit_current_url": StringDatabaseField(name="$exit_current_url"),
    "$exit_pathname": StringDatabaseField(name="$exit_pathname"),
    "$exit_hostname": StringDatabaseField(name="$exit_host"),
    "$entry_referring_domain": StringDatabaseField(name="$entry_referring_domain"),
    # UTM parameters
    "$entry_utm_source": StringDatabaseField(name="$entry_utm_source"),
    "$entry_utm_campaign": StringDatabaseField(name="$entry_utm_campaign"),
    "$entry_utm_medium": StringDatabaseField(name="$entry_utm_medium"),
    "$entry_utm_term": StringDatabaseField(name="$entry_utm_term"),
    "$entry_utm_content": StringDatabaseField(name="$entry_utm_content"),
    # Other Ad / campaign / attribution IDs
    "$entry_gclid": StringDatabaseField(name="$entry_gclid"),
    "$entry_gad_source": StringDatabaseField(name="$entry_gad_source"),
    "$entry_gclsrc": StringDatabaseField(name="$entry_gclsrc"),
    "$entry_dclid": StringDatabaseField(name="$entry_dclid"),
    "$entry_gbraid": StringDatabaseField(name="$entry_gbraid"),
    "$entry_wbraid": StringDatabaseField(name="$entry_wbraid"),
    "$entry_fbclid": StringDatabaseField(name="$entry_fbclid"),
    "$entry_msclkid": StringDatabaseField(name="$entry_msclkid"),
    "$entry_twclid": StringDatabaseField(name="$entry_twclid"),
    "$entry_li_fat_id": StringDatabaseField(name="$entry_li_fat_id"),
    "$entry_mc_cid": StringDatabaseField(name="$entry_mc_cid"),
    "$entry_igshid": StringDatabaseField(name="$entry_igshid"),
    "$entry_ttclid": StringDatabaseField(name="$entry_ttclid"),
    # Counts (used in e.g. bounce rate)
    "$event_count_map": DatabaseField(name="$event_count_map"),
    "$pageview_count": IntegerDatabaseField(name="$pageview_count"),
    "$autocapture_count": IntegerDatabaseField(name="$autocapture_count"),
    # Derived
    "$channel_type": StringDatabaseField(name="$channel_type"),
    "$session_duration": IntegerDatabaseField(name="$session_duration"),
    "duration": IntegerDatabaseField(
        name="duration"
    ),  # alias of $session_duration, deprecated but included for backwards compatibility
    "$is_bounce": BooleanDatabaseField(name="$is_bounce"),
    # some aliases for people reverting from v2 to v1
    "$end_current_url": StringDatabaseField(name="$end_current_url"),
    "$end_pathname": StringDatabaseField(name="$end_pathname"),
}


class RawSessionsTableV1(Table):
    fields: dict[str, FieldOrTable] = RAW_SESSIONS_FIELDS

    def to_printed_clickhouse(self, context):
        return "sessions"

    def to_printed_hogql(self):
        return "raw_sessions"

    def avoid_asterisk_fields(self) -> list[str]:
        # our clickhouse driver can't return aggregate states
        return [
            "entry_url",
            "exit_url",
            "initial_utm_source",
            "initial_utm_campaign",
            "initial_utm_medium",
            "initial_utm_term",
            "initial_utm_content",
            "initial_referring_domain",
            "initial_gclid",
            "initial_gad_source",
            "initial_gclsrc",
            "initial_dclid",
            "initial_gbraid",
            "initial_wbraid",
            "initial_fbclid",
            "initial_msclkid",
            "initial_twclid",
            "initial_li_fat_id",
            "initial_mc_cid",
            "initial_igshid",
            "initial_ttclid",
        ]


def select_from_sessions_table_v1(
    requested_fields: dict[str, list[str | int]], node: ast.SelectQuery, context: HogQLContext
):
    from posthog.hogql import ast

    table_name = "raw_sessions"

    # Always include "session_id", as it's the key we use to make further joins, and it'd be great if it's available
    if "session_id" not in requested_fields:
        requested_fields = {**requested_fields, "session_id": ["session_id"]}

    def arg_min_merge_field(field_name: str) -> ast.Call:
        return ast.Call(
            name="nullIf",
            args=[
                ast.Call(name="argMinMerge", args=[ast.Field(chain=[table_name, field_name])]),
                ast.Constant(value="null"),
            ],
        )

    def arg_max_merge_field(field_name: str) -> ast.Call:
        return ast.Call(
            name="nullIf",
            args=[
                ast.Call(name="argMaxMerge", args=[ast.Field(chain=[table_name, field_name])]),
                ast.Constant(value="null"),
            ],
        )

    aggregate_fields: dict[str, ast.Expr] = {
        "distinct_id": ast.Call(name="any", args=[ast.Field(chain=[table_name, "distinct_id"])]),
        "$start_timestamp": ast.Call(name="min", args=[ast.Field(chain=[table_name, "min_timestamp"])]),
        "$end_timestamp": ast.Call(name="max", args=[ast.Field(chain=[table_name, "max_timestamp"])]),
        "$urls": ast.Call(
            name="arrayDistinct",
            args=[
                ast.Call(
                    name="arrayFlatten",
                    args=[ast.Call(name="groupArray", args=[ast.Field(chain=[table_name, "urls"])])],
                )
            ],
        ),
        "$entry_current_url": null_if_empty(arg_min_merge_field("entry_url")),
        "$exit_current_url": null_if_empty(arg_max_merge_field("exit_url")),
        "$entry_utm_source": null_if_empty(arg_min_merge_field("initial_utm_source")),
        "$entry_utm_campaign": null_if_empty(arg_min_merge_field("initial_utm_campaign")),
        "$entry_utm_medium": null_if_empty(arg_min_merge_field("initial_utm_medium")),
        "$entry_utm_term": null_if_empty(arg_min_merge_field("initial_utm_term")),
        "$entry_utm_content": null_if_empty(arg_min_merge_field("initial_utm_content")),
        "$entry_referring_domain": null_if_empty(arg_min_merge_field("initial_referring_domain")),
        "$entry_gclid": null_if_empty(arg_min_merge_field("initial_gclid")),
        "$entry_gad_source": null_if_empty(arg_min_merge_field("initial_gad_source")),
        "$entry_gclsrc": null_if_empty(arg_min_merge_field("initial_gclsrc")),
        "$entry_dclid": null_if_empty(arg_min_merge_field("initial_dclid")),
        "$entry_gbraid": null_if_empty(arg_min_merge_field("initial_gbraid")),
        "$entry_wbraid": null_if_empty(arg_min_merge_field("initial_wbraid")),
        "$entry_fbclid": null_if_empty(arg_min_merge_field("initial_fbclid")),
        "$entry_msclkid": null_if_empty(arg_min_merge_field("initial_msclkid")),
        "$entry_twclid": null_if_empty(arg_min_merge_field("initial_twclid")),
        "$entry_li_fat_id": null_if_empty(arg_min_merge_field("initial_li_fat_id")),
        "$entry_mc_cid": null_if_empty(arg_min_merge_field("initial_mc_cid")),
        "$entry_igshid": null_if_empty(arg_min_merge_field("initial_igshid")),
        "$entry_ttclid": null_if_empty(arg_min_merge_field("initial_ttclid")),
        "$event_count_map": ast.Call(
            name="sumMap",
            args=[ast.Field(chain=[table_name, "event_count_map"])],
        ),
        "$pageview_count": ast.Call(name="sum", args=[ast.Field(chain=[table_name, "pageview_count"])]),
        "$autocapture_count": ast.Call(name="sum", args=[ast.Field(chain=[table_name, "autocapture_count"])]),
    }
    # Some fields are calculated from others. It'd be good to actually deduplicate common sub expressions in SQL, but
    # for now just remove the duplicate definitions from the code
    aggregate_fields["$entry_pathname"] = ast.Call(
        name="path",
        args=[aggregate_fields["$entry_current_url"]],
    )
    aggregate_fields["$entry_hostname"] = ast.Call(
        name="domain",
        args=[aggregate_fields["$entry_current_url"]],
    )
    aggregate_fields["$exit_pathname"] = ast.Call(
        name="path",
        args=[aggregate_fields["$exit_current_url"]],
    )
    aggregate_fields["$exit_hostname"] = ast.Call(
        name="domain",
        args=[aggregate_fields["$exit_current_url"]],
    )
    aggregate_fields["$session_duration"] = ast.Call(
        name="dateDiff",
        args=[
            ast.Constant(value="second"),
            aggregate_fields["$start_timestamp"],
            aggregate_fields["$end_timestamp"],
        ],
    )
    aggregate_fields["duration"] = aggregate_fields["$session_duration"]
    aggregate_fields["$num_uniq_urls"] = ast.Call(
        name="length",
        args=[aggregate_fields["$urls"]],
    )

    bounce_rate_duration_seconds = (
        context.modifiers.bounceRateDurationSeconds
        if context.modifiers.bounceRateDurationSeconds is not None
        else DEFAULT_BOUNCE_RATE_DURATION_SECONDS
    )
    if context.modifiers.bounceRatePageViewMode == BounceRatePageViewMode.UNIQ_URLS:
        bounce_pageview_count = aggregate_fields["$num_uniq_urls"]
    else:
        bounce_pageview_count = aggregate_fields["$pageview_count"]
    aggregate_fields["$is_bounce"] = ast.Call(
        name="if",
        args=[
            # if pageview_count is 0, return NULL so it doesn't contribute towards the bounce rate either way
            ast.Call(name="equals", args=[bounce_pageview_count, ast.Constant(value=0)]),
            ast.Constant(value=None),
            ast.Call(
                name="not",
                args=[
                    ast.Call(
                        name="or",
                        args=[
                            # if > 1 pageview, not a bounce
                            ast.Call(name="greater", args=[bounce_pageview_count, ast.Constant(value=1)]),
                            # if > 0 autocapture events, not a bounce
                            ast.Call(
                                name="greater", args=[aggregate_fields["$autocapture_count"], ast.Constant(value=0)]
                            ),
                            # if session duration >= bounce_rate_duration_seconds, not a bounce
                            ast.Call(
                                name="greaterOrEquals",
                                args=[
                                    aggregate_fields["$session_duration"],
                                    ast.Constant(value=bounce_rate_duration_seconds),
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    aggregate_fields["$channel_type"] = create_channel_type_expr(
        context.modifiers.customChannelTypeRules,
        ChannelTypeExprs(
            campaign=aggregate_fields["$entry_utm_campaign"],
            medium=aggregate_fields["$entry_utm_medium"],
            source=aggregate_fields["$entry_utm_source"],
            referring_domain=aggregate_fields["$entry_referring_domain"],
            url=aggregate_fields["$entry_current_url"],
            hostname=aggregate_fields["$entry_hostname"],
            pathname=aggregate_fields["$entry_pathname"],
            has_gclid=ast.Call(
                name="isNotNull",
                args=[aggregate_fields["$entry_gclid"]],
            ),
            has_fbclid=ast.Call(
                name="isNotNull",
                args=[aggregate_fields["$entry_fbclid"]],
            ),
            gad_source=aggregate_fields["$entry_gad_source"],
        ),
        timings=context.timings,
    )

    # aliases for people reverting from v2 to v1
    aggregate_fields["$end_current_url"] = aggregate_fields["$exit_current_url"]
    aggregate_fields["$end_pathname"] = aggregate_fields["$exit_pathname"]

    select_fields: list[ast.Expr] = []
    group_by_fields: list[ast.Expr] = [ast.Field(chain=[table_name, "session_id"])]

    for name, chain in requested_fields.items():
        if name in aggregate_fields:
            select_fields.append(ast.Alias(alias=name, expr=aggregate_fields[name]))
        else:
            select_fields.append(
                ast.Alias(alias=name, expr=ast.Field(chain=cast(list[str | int], [table_name]) + chain))
            )
            group_by_fields.append(ast.Field(chain=cast(list[str | int], [table_name]) + chain))

    where = SessionMinTimestampWhereClauseExtractorV1(context).get_inner_where(node)

    return ast.SelectQuery(
        select=select_fields,
        select_from=ast.JoinExpr(table=ast.Field(chain=[table_name])),
        group_by=group_by_fields,
        where=where,
    )


class SessionsTableV1(LazyTable):
    fields: dict[str, FieldOrTable] = LAZY_SESSIONS_FIELDS

    def lazy_select(
        self,
        table_to_add: LazyTableToAdd,
        context,
        node: ast.SelectQuery,
    ):
        return select_from_sessions_table_v1(table_to_add.fields_accessed, node, context)

    def to_printed_clickhouse(self, context):
        return "sessions"

    def to_printed_hogql(self):
        return "sessions"

    def avoid_asterisk_fields(self) -> list[str]:
        return [
            "duration",  # alias of $session_duration, deprecated but included for backwards compatibility
            # aliases for people reverting from v2 to v1
            "$end_current_url",
            "$end_pathname",
        ]


def join_events_table_to_sessions_table(
    join_to_add: LazyJoinToAdd, context: HogQLContext, node: ast.SelectQuery
) -> ast.JoinExpr:
    from posthog.hogql import ast

    if not join_to_add.fields_accessed:
        raise ResolutionError("No fields requested from events")

    join_expr = ast.JoinExpr(table=select_from_sessions_table_v1(join_to_add.fields_accessed, node, context))
    join_expr.join_type = "LEFT JOIN"
    join_expr.alias = join_to_add.to_table
    join_expr.constraint = ast.JoinConstraint(
        expr=ast.CompareOperation(
            op=ast.CompareOperationOp.Eq,
            left=ast.Field(chain=[join_to_add.from_table, "$session_id"]),
            right=ast.Field(chain=[join_to_add.to_table, "session_id"]),
        ),
        constraint_type="ON",
    )
    return join_expr


def get_lazy_session_table_properties_v1(search: Optional[str]):
    # some fields shouldn't appear as properties
    hidden_fields = {
        "team_id",
        "distinct_id",
        "session_id",
        "id",
        "$event_count_map",
        "$urls",
        "duration",
        "$num_uniq_urls",
        # aliases for people reverting from v2 to v1
        "$end_current_url",
        "$end_pathname",
    }

    # some fields should have a specific property type which isn't derivable from the type of database field
    property_type_overrides = {
        "$session_duration": PropertyType.Duration,
    }

    def get_property_type(field_name: str, field_definition: FieldOrTable):
        if field_name in property_type_overrides:
            return property_type_overrides[field_name]
        if isinstance(field_definition, IntegerDatabaseField) or isinstance(field_definition, FloatDatabaseField):
            return PropertyType.Numeric
        if isinstance(field_definition, DateTimeDatabaseField):
            return PropertyType.Datetime
        if isinstance(field_definition, BooleanDatabaseField):
            return PropertyType.Boolean
        return PropertyType.String

    search_words = re.findall(r"\w+", search.lower()) if search else None

    def is_match(field_name: str) -> bool:
        if field_name in hidden_fields:
            return False
        if not search_words:
            return True
        return all(word in field_name.lower() for word in search_words)

    results = [
        {
            "id": field_name,
            "name": field_name,
            "is_numerical": isinstance(field_definition, IntegerDatabaseField)
            or isinstance(field_definition, FloatDatabaseField),
            "property_type": get_property_type(field_name, field_definition),
            "is_seen_on_filtered_events": None,
            "tags": [],
        }
        for field_name, field_definition in LAZY_SESSIONS_FIELDS.items()
        if is_match(field_name)
    ]
    return results


# NOTE: Keep the AD IDs in sync with `posthog.hogql_queries.web_analytics.session_attribution_explorer_query_runner.py`
SESSION_PROPERTY_TO_RAW_SESSIONS_EXPR_MAP = {
    "$entry_referring_domain": "finalizeAggregation(initial_referring_domain)",
    "$entry_utm_source": "finalizeAggregation(initial_utm_source)",
    "$entry_utm_campaign": "finalizeAggregation(initial_utm_campaign)",
    "$entry_utm_medium": "finalizeAggregation(initial_utm_medium)",
    "$entry_utm_term": "finalizeAggregation(initial_utm_term)",
    "$entry_utm_content": "finalizeAggregation(initial_utm_content)",
    "$entry_gclid": "finalizeAggregation(initial_gclid)",
    "$entry_gad_source": "finalizeAggregation(initial_gad_source)",
    "$entry_gclsrc": "finalizeAggregation(initial_gclsrc)",
    "$entry_dclid": "finalizeAggregation(initial_dclid)",
    "$entry_gbraid": "finalizeAggregation(initial_gbraid)",
    "$entry_wbraid": "finalizeAggregation(initial_wbraid)",
    "$entry_fbclid": "finalizeAggregation(initial_fbclid)",
    "$entry_msclkid": "finalizeAggregation(initial_msclkid)",
    "$entry_twclid": "finalizeAggregation(initial_twclid)",
    "$entry_li_fat_id": "finalizeAggregation(initial_li_fat_id)",
    "$entry_mc_cid": "finalizeAggregation(initial_mc_cid)",
    "$entry_igshid": "finalizeAggregation(initial_igshid)",
    "$entry_ttclid": "finalizeAggregation(initial_ttclid)",
    "$entry_current_url": "finalizeAggregation(entry_url)",
    "$exit_current_url": "finalizeAggregation(exit_url)",
}


def get_lazy_session_table_values_v1(key: str, search_term: Optional[str], team: "Team"):
    # the sessions table does not have a properties json object like the events and person tables

    if key == "$channel_type":
        return [[entry] for entry in DEFAULT_CHANNEL_TYPES if not search_term or search_term.lower() in entry.lower()]

    field_definition = LAZY_SESSIONS_FIELDS.get(key)
    if not field_definition:
        return []

    if isinstance(field_definition, StringDatabaseField):
        expr = SESSION_PROPERTY_TO_RAW_SESSIONS_EXPR_MAP.get(key)

        if not expr:
            return []

        if search_term:
            return insight_sync_execute(
                SELECT_SESSION_PROP_STRING_VALUES_SQL_WITH_FILTER.format(property_expr=expr),
                {"team_id": team.pk, "key": key, "value": "%{}%".format(search_term)},
                query_type="get_session_property_values_with_value",
                team_id=team.pk,
            )
        return insight_sync_execute(
            SELECT_SESSION_PROP_STRING_VALUES_SQL.format(property_expr=expr),
            {"team_id": team.pk, "key": key},
            query_type="get_session_property_values",
            team_id=team.pk,
        )
    if isinstance(field_definition, BooleanDatabaseField):
        # ideally we'd be able to just send [[True], [False]]
        return [["1"], ["0"]]

    return []


def null_if_empty(expr: ast.Expr) -> ast.Call:
    return ast.Call(name="nullIf", args=[expr, ast.Constant(value="")])
