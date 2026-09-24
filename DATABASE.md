# Database shape — student weightage

The Weights card and the Performance card on the student profile are driven
entirely by MongoDB: the page loads them live from
`GET /api/student/weights/{registerNumber}`, and the total is the sum of exactly
the rows shown in the Weights card.

## Where

| Item        | Value                                    |
|-------------|------------------------------------------|
| Database    | `Cluster0` (`DB_NAME` in `insert_mongo.py`) |
| Collection  | `students` (`COLLECTION_NAME`)           |
| Granularity | one document per student                 |
| Lookup key  | `RegNumber` (indexed as `regnumber_idx`, matched case-insensitively) |

## The `Weightage` field

`Weightage` is an **array of embedded documents** at the top level of the
student document, next to `Scores`, `MentorFeedback`, etc.

```jsonc
{
  "RegNumber": "DDAICS047",
  "StudentName": "Nayana N K",
  "Branch": "CS",
  "Scores": { "AI": { "...": 0 }, "DevOps": { "...": 0 } },

  "Weightage": [
    { "AssessmentType": "Assignments",     "Weightage": 30 },
    { "AssessmentType": "Responsiveness",  "Weightage": 10 },
    { "AssessmentType": "Assessments",     "Weightage": 40 },
    { "AssessmentType": "Mock Interview",  "Weightage": 20 }
  ]
}
```

### Field rules

| Path                       | Type            | Rules |
|----------------------------|-----------------|-------|
| `Weightage`                | array           | Optional. Missing / not an array is treated as `[]`. |
| `Weightage[].AssessmentType` | string        | Required, non-empty, trimmed. **Unique within the array, case-insensitive** (`Quiz` and `quiz` are the same entry). |
| `Weightage[].Weightage`    | number (double) | Required, `0`–`100`, stored rounded to 2 decimals. Percent, not a fraction (`20`, not `0.2`). |

Notes

- **No weight topics are hardcoded** anywhere (frontend or backend). The
  Weights card lists exactly the entries stored in `Weightage`; a student with
  no entries sees the empty state. `Assignments`, `Responsiveness`, `Assessments`
  and `Mock Interview` above are only an example — use whatever topics you need.
- The sum is **not** enforced to equal 100 anywhere. The Performance card
  shows the plain sum of the stored entries; the API also returns it as
  `totalWeightage`.
- Anything that does not fit the rules (non-object entries, blank type,
  non-numeric weight, duplicate type) is skipped by
  `data.get_student_weights_summary`. For duplicates the first one wins.

## API contract

`GET /api/student/weights/{registerNumber}`

```json
{
  "weights": [
    { "assessmentType": "Assignments", "weightage": 30.0 },
    { "assessmentType": "Assessments", "weightage": 40.0 }
  ],
  "totalWeightage": 70.0
}
```

`404` if the student does not exist or the DB call failed. The existing
`POST /api/student/weight` (upsert) and `DELETE /api/student/weight` endpoints
are unchanged; the "+", edit and delete buttons on the Weights card use them.

## Managing the data (mongosh)

```js
// set all weights for a student
db.students.updateOne(
  { RegNumber: "DDAICS047" },
  { $set: { Weightage: [
      { AssessmentType: "Assignments",    Weightage: 30 },
      { AssessmentType: "Responsiveness", Weightage: 10 },
      { AssessmentType: "Assessments",    Weightage: 40 },
      { AssessmentType: "Mock Interview", Weightage: 20 }
  ] } }
)

// add one (make sure the type is not already present)
db.students.updateOne(
  { RegNumber: "DDAICS047", "Weightage.AssessmentType": { $ne: "Quiz" } },
  { $push: { Weightage: { AssessmentType: "Quiz", Weightage: 10 } } }
)

// change one
db.students.updateOne(
  { RegNumber: "DDAICS047", "Weightage.AssessmentType": "Quiz" },
  { $set: { "Weightage.$.Weightage": 25 } }
)

// remove one
db.students.updateOne(
  { RegNumber: "DDAICS047" },
  { $pull: { Weightage: { AssessmentType: "Quiz" } } }
)

// read the total straight from the DB
db.students.aggregate([
  { $match: { RegNumber: "DDAICS047" } },
  { $project: { _id: 0, RegNumber: 1, Weightage: 1,
                totalWeightage: { $sum: "$Weightage.Weightage" } } }
])

// find students whose weights do not add up to 100
db.students.aggregate([
  { $project: { RegNumber: 1, total: { $sum: "$Weightage.Weightage" } } },
  { $match: { total: { $ne: 100 } } }
])
```

## Optional: let MongoDB validate the shape

Only the `Weightage` field is constrained; other fields are untouched. It is set
to *warn* (log, don't reject) so existing documents keep working.

```js
db.runCommand({
  collMod: "students",
  validator: { $jsonSchema: {
    bsonType: "object",
    properties: {
      Weightage: {
        bsonType: "array",
        items: {
          bsonType: "object",
          required: ["AssessmentType", "Weightage"],
          properties: {
            AssessmentType: { bsonType: "string", minLength: 1 },
            Weightage: { bsonType: ["double", "int", "long", "decimal"], minimum: 0, maximum: 100 }
          }
        }
      }
    }
  } },
  validationLevel: "moderate",
  validationAction: "warn"
})
```

Uniqueness of `AssessmentType` inside the array cannot be expressed in a
JSON Schema validator; the write path in `insert_mongo.add_student_weight`
enforces it (case-insensitive upsert).

## Where the weights show up

- **Student roster page:** the columns are `... Email | Average score | Performance |
  Watchlist`. **Performance** is the total of the student's `Weightage` entries
  (e.g. `70%`, or `—` if they have none); it replaced the AI score and DevOps score
  columns, and the sort dropdown has "Performance: High to Low" in place of the
  AI/DevOps score sorts. The value comes from the roster loaded from Mongo
  (`totalWeightage` / `hasWeights` on each roster entry).
- **Student profile page:** the Weights card and the **Performance** card (the total
  of those weights), loaded live
  from `GET /api/student/weights/{registerNumber}`.

