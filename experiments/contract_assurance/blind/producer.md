# Blind producer task

Read only the supplied frozen public package. Produce exactly the requested structured response for the case/input. Do not inspect repository implementation, tests, prior results, hidden artifacts, or validator behavior. Return only the requested output; do not explain the contract.

The worker is compliant with this instruction only if the batch audit independently records repository access disabled and implementation access unavailable. Otherwise label the batch `NOT_BLIND` and exclude it from compliance statistics.
