import dataclasses
import json
from typing import List, Optional, Type, TypeVar, Literal
from datetime import datetime

# Define a type variable for our dataclasses
T = TypeVar('T')

# Define the dataclasses as before
@dataclasses.dataclass
class InlineCommentMessageType:
    author_username: str
    author_display_name: str
    message: str
    date: datetime

@dataclasses.dataclass
class InlineCommentType:
    file: str
    line_no: int
    messages: List[InlineCommentMessageType]

@dataclasses.dataclass
class MergeProposalCommentType:
    id: str
    self_link: str
    author_username: str
    message: str
    date_created: str
    date_last_edited: Optional[str] = None

@dataclasses.dataclass
class DiffStatType:
    file: str
    additions: int
    deletions: int

@dataclasses.dataclass
class DiffType:
    id: str
    self_link: str
    diff_stats: List[DiffStatType] = dataclasses.field(default_factory=list)
    inline_comments: List[InlineCommentType] = dataclasses.field(default_factory=list)
    diff_text: Optional[str] = None

@dataclasses.dataclass
class MergeProposalReviewVote():
    reviewer_username: str
    reviewer_display_name: str
    vote: Optional[Literal["APPROVE", "NEEDS_FIXING", "NEEDS_INFO", "ABSTAIN", "DISAPPROVE", "NEEDS_RESUBMITTING"]] = None
    needs_reviewer: bool = False

@dataclasses.dataclass
class MergeProposalType:
    id: str
    self_link: str
    repo_name: str
    url: str
    source_git_url: str
    target_git_url: str
    source_branch: str
    target_branch: str
    source_owner: str
    target_owner: str
    review_state: str
    diffs: List[DiffType] = dataclasses.field(default_factory=list)
    reviewers: Optional[List[str]] = dataclasses.field(default_factory=list)
    description: Optional[str] = None
    commit_message: Optional[str] = None
    ci_cd_status: Literal["PASSING", "FAILING", "UNKNOWN"] = "UNKNOWN"
    jira_tickets: Optional[List[str]] = dataclasses.field(default_factory=list)
    comments: List[MergeProposalCommentType] = dataclasses.field(default_factory=list)
    review_votes: List[MergeProposalReviewVote] = dataclasses.field(default_factory=list) 


def from_dict(data_class: Type[T], data: dict) -> T:
    try:
        # Handle special case for datetime field
        if isinstance(data_class, InlineCommentMessageType):
            data['date'] = datetime.fromisoformat(data['date'])
        # Recursively convert dictionaries to dataclasses
        fieldtypes = {f.name: f.type for f in dataclasses.fields(data_class)}
        return data_class(**{
            f: from_dict(fieldtypes[f], data[f]) if dataclasses.is_dataclass(fieldtypes[f]) else data[f]
            for f in data
        })
    except (TypeError, ValueError) as e:
        raise ValueError(f"Error converting dictionary to {data_class}: {e}")

def to_dict(instance: dataclasses.dataclass) -> dict:
    if dataclasses.is_dataclass(instance):
        result = {}
        for field in dataclasses.fields(instance):
            value = getattr(instance, field.name)
            if dataclasses.is_dataclass(value):
                result[field.name] = to_dict(value)
            elif isinstance(value, list):
                result[field.name] = [to_dict(i) if dataclasses.is_dataclass(i) else i for i in value]
            elif isinstance(value, datetime):
                result[field.name] = value.isoformat()
            else:
                result[field.name] = value
        return result
    raise TypeError("to_dict() should be called on dataclass instances")

def from_json(data_class: Type[T], json_data: str) -> T:
    try:
        return from_dict(data_class, json.loads(json_data))
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON: {e}")

def to_json(instance: dataclasses.dataclass) -> str:
    return json.dumps(to_dict(instance), ensure_ascii=False)

# # Example usage:
# # Convert dataclass instance to JSON
# merge_proposal = MergeProposalType(
#     repo_name="example-repo",
#     url="http://example.com",
#     source_git_url="http://example.com/src.git",
#     target_git_url="http://example.com/tgt.git",
#     source_branch="develop",
#     target_branch="master",
#     source_owner="user1",
#     target_owner="user2",
#     review_state="pending",
#     inline_comments_count=5,
#     diff_stats=[DiffStatType(file="file1.py", additions=10, deletions=2)],
#     reviewers=["user3", "user4"]
# )
# json_data = to_json(merge_proposal)
# print(json_data)

# # Convert JSON back to dataclass instance
# json_string = '{"repo_name": "example-repo", "url": "http://example.com", ...}' # Complete with actual JSON
# new_merge_proposal = from_json(MergeProposalType, json_string)
# print(new_merge_proposal)