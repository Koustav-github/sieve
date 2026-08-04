from dataclasses import dataclass


@dataclass(frozen=True)
class FineBucket:
    name: str
    description: str
    keywords: list[str]
    exemplars: list[str]


FINE_BUCKETS: dict[str, list[FineBucket]] = {
    "careers": [
        FineBucket(
            name="job-seeking",
            description=(
                "Someone actively looking for a job opportunity at the company - a "
                "general inquiry about openings or an unsolicited application, not "
                "tied to one named role."
            ),
            keywords=[
                "job opening",
                "hiring",
                "position available",
                "job application",
                "vacancy",
                "job hunting",
                "resume",
            ],
            exemplars=[
                "Hi, I saw your company is hiring and I'd love to apply.",
                "Are there any open positions right now?",
                "I'm interested in joining your team, do you have any vacancies?",
                "Please find my resume attached for any suitable role.",
                "I'm currently job hunting and your company caught my eye.",
            ],
        ),
        FineBucket(
            name="specific-opening",
            description=(
                "References a specific, named job posting or requisition the "
                "company advertised, rather than a general inquiry."
            ),
            keywords=[
                "regarding the job posting",
                "job id",
                "req id",
                "posting for",
                "the role of",
            ],
            exemplars=[
                "I'm applying for the Senior Backend Engineer role posted on your careers page.",
                "This is regarding job ID SWE-2024-103.",
                "I saw the opening for a Product Designer and wanted to submit my application.",
                "Following up on the Data Analyst position listed on LinkedIn.",
                "I'd like to apply for the specific role of DevOps Engineer mentioned in your posting.",
            ],
        ),
        FineBucket(
            name="referral",
            description=(
                "Referring or recommending a candidate for a role, typically from "
                "an employee, friend, or acquaintance rather than the candidate "
                "themselves."
            ),
            keywords=[
                "referral",
                "referring",
                "would like to refer",
                "my friend works at",
                "referred by",
            ],
            exemplars=[
                "I'd like to refer a friend of mine for the open engineering role.",
                "My colleague is job hunting, can I refer her to your team?",
                "Referral: John Doe would be a great fit for your support position.",
                "I'm recommending a former teammate for your open position.",
                "Would love to refer someone I know for the marketing role.",
            ],
        ),
        FineBucket(
            name="recruiter-inbound",
            description=(
                "A recruiter, staffing agency, or headhunter reaching out on "
                "behalf of candidates or offering recruiting services."
            ),
            keywords=[
                "recruiter",
                "talent acquisition",
                "staffing agency",
                "headhunter",
                "on behalf of a candidate",
            ],
            exemplars=[
                "Hi, I'm a recruiter and I have a great candidate for your open role.",
                "I work with a staffing agency and wanted to discuss your hiring needs.",
                "As a headhunter, I specialize in placing engineers like the one you're looking for.",
                "I'd like to introduce you to a few pre-vetted candidates for your open positions.",
                "Reaching out from XYZ Recruiting on behalf of a strong candidate match.",
            ],
        ),
    ],
    "support": [
        FineBucket(
            name="customer-complaints",
            description=(
                "Expressing dissatisfaction or reporting a problem with a "
                "product, service, or order."
            ),
            keywords=[
                "complaint",
                "not working",
                "disappointed",
                "refund",
                "unacceptable",
                "broken",
            ],
            exemplars=[
                "I'm very disappointed with the product I received, it's broken.",
                "This is a complaint about the poor service I got yesterday.",
                "My order arrived damaged and I want a refund.",
                "I've had nothing but issues since I signed up.",
                "Unacceptable delay on my shipment, please fix this.",
            ],
        ),
        FineBucket(
            name="suggestions",
            description=(
                "Proposing an improvement, new feature, or idea rather than "
                "reporting a problem."
            ),
            keywords=[
                "suggestion",
                "it would be great if",
                "feature request",
                "you should add",
                "idea for improvement",
            ],
            exemplars=[
                "It would be great if you added dark mode to the app.",
                "Just a suggestion: maybe add a bulk export feature.",
                "I have an idea that could improve your onboarding flow.",
                "Feature request: support for multiple currencies.",
                "You should consider adding a mobile app for this.",
            ],
        ),
        FineBucket(
            name="review",
            description=(
                "General feedback or a review of the product/service, positive "
                "or neutral, not primarily a complaint or feature request."
            ),
            keywords=[
                "review",
                "rating",
                "five stars",
                "great experience",
                "highly recommend",
            ],
            exemplars=[
                "Just wanted to leave a review, great experience overall!",
                "Five stars, would recommend to anyone.",
                "Sharing my overall experience using your product for the past month.",
                "Great product, here's my honest review.",
                "Wanted to give feedback on my experience so far.",
            ],
        ),
    ],
    "internal": [
        FineBucket(
            name="welfare",
            description=(
                "Staff personal wellbeing, mental health, or a personal "
                "circumstance needing support."
            ),
            keywords=[
                "mental health",
                "wellbeing",
                "burnout",
                "leave of absence",
                "personal emergency",
            ],
            exemplars=[
                "I've been struggling with burnout lately and need to talk.",
                "Requesting a leave of absence for a personal emergency.",
                "I wanted to flag that I'm not doing well mentally right now.",
                "Can we discuss options for wellbeing support?",
                "I need some time off to deal with a family emergency.",
            ],
        ),
        FineBucket(
            name="work-life",
            description=(
                "Scheduling, remote work, time off, or balancing work and "
                "personal life."
            ),
            keywords=[
                "work from home",
                "flexible hours",
                "schedule change",
                "vacation request",
                "PTO",
            ],
            exemplars=[
                "Requesting to work from home next week.",
                "Can I shift my hours to start later in the day?",
                "Submitting my PTO request for next month.",
                "I'd like to discuss flexible working arrangements.",
                "Following up on my vacation request from last week.",
            ],
        ),
        FineBucket(
            name="employee-security",
            description=(
                "Reporting a safety, security, or harassment concern involving "
                "staff, requiring careful and confidential handling."
            ),
            keywords=[
                "harassment",
                "safety concern",
                "security incident",
                "report a concern",
                "confidential complaint",
            ],
            exemplars=[
                "I need to report a harassment incident involving a colleague.",
                "There's a safety concern in the office I want to flag.",
                "This is a confidential report about a security incident.",
                "I want to raise a concern about my safety at work.",
                "Reporting an incident that made me feel unsafe.",
            ],
        ),
    ],
}
