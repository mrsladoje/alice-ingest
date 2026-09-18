# Style brief: the voice of the report

Source of truth: reports/inputs/deck-text.md. Every sentence there was hand-audited by the author. The Roko digest (reports/digests/roko.md) is the second reference. The Sotiris digest shows the voice to avoid.

## Rules, each with a real sentence from the deck

1. State the conclusion first, then the support.
   "InfoLogger moves the messages a process sends it. That is one road. Most of what a node writes never takes it." (slide 3)
   "Ansible won because it leaves nothing running on the five machines." (slide 11)

2. One idea per sentence. Most sentences are under 15 words. A 25-word sentence is the ceiling.
   "It reads each line, decides where the line belongs and sends it on. It stores nothing." (slide 7)

3. Name the fact that decided a choice, in one short sentence.
   "Maturity and size decided it." (slide 7) "Detection decided it." (slide 8) "The agent decided it." (slide 11) "An open licence was the floor." (slide 8)

4. Name the alternative and say why it lost, in the same breath.
   "Puppet is likely right for the farm. It is wrong for five machines that one person deploys by hand." (slide 11, semicolon replaced)
   "A Ruby package tree on every worker is what we did not want." (slide 7)
   "Shell scripts were never a candidate, because a script does a thing but never checks the thing." (slide 11)

5. Define a term the moment it appears, with a colon, an appositive, or one short sentence.
   "k-NN: k nearest neighbours. Distance is the score." (slide 13)
   "An agent is a program the tool installs and leaves running on every machine." (slide 11)
   "KRaft: Kafka keeps its own cluster state, with no separate coordination service beside it." (slide 15)
   "ISM, Index State Management: indices roll on a clock, old ones are deleted." (slide 10)

6. Every number carries its meaning and its reference.
   "It takes four of the 128 cores an EPN has, and little memory." (slide 4)
   "The 450 KB is the vendor's figure for an idle pipeline; ours peaked between 133 and 228 MB under load." (slide 7)
   "Routine logs 8 days, others 35, InfoLogger 56." (slide 10)

7. Say plainly what is not built, not measured, or not decided.
   "Nothing here is built. Three ways, none chosen." (slide 13)
   "The shifter's own view is not built." (slide 12)
   "Fanout: not everything is perfect." (slide 6)

8. Use "we" for the project's decisions and "a person", "a shifter", "a physicist" for people.
   "We split some log families by severity, not by source." (slide 4)
   "We run one cluster. The other way is many clusters, joined together by cross-cluster search." (slide 6)
   "A physicist with a real question." (slide 12)

9. Prefer the concrete noun: machine, worker, wire, disk, line, record. Avoid host, VM, instance, node in prose unless the distinction matters.
   "The bulk of the logs never leaves the machine that wrote them. The rest is copied onto three machines." (slide 10)
   "Routine info logs are the bulk. They never cross the wire." (slide 10)

10. Let a component act. Short active verbs: reads, sends, lands, asks, answers, holds, drops, pushes.
    "Every worker pushes its own numbers up, and nothing goes out to ask." (slide 14)
    "Nothing polls the worker. No model runs on it." (slide 13)
    "A broker moves records. It does not make them." (slide 7)

11. State a trade-off as two costs, each in its own sentence.
    "Every machine pays. One search asks all of them, so all of them spend processor time on it. It is as slow as the slowest machine." (slide 6)
    "It fixes the cost. It inherits some of the saturation." (slide 13)

12. Contrast pairs are allowed when both halves carry a fact.
    "That puts the volume on one side and the value on the other." (slide 4)
    "Push up, never poll down." (slide 6)
    "Zero copies of the cheap half. Three copies of the valuable half." (slide 10)

13. Explain a mechanism as a short causal chain with "so" and "then".
    "The cluster needs two of the three to agree, so it keeps running." (slide 10)
    "Extract the skeletons first. Then run the distance test on the templates, and not on the lines." (slide 13)

14. Give the plain goal of a component before its details.
    "A lightweight forwarder runs on the edge beside the program that writes the logs." (slide 7)
    "We need a search engine that indexes every field and the message body, counts them over time, and brings anomaly detection with it." (slide 8)

15. Close a section on a fact, never on a summary or a flourish.
    "Costs three brokers and a second system to run. Memory these five machines do not have." (slide 15)

## Words and forms

- British spelling, as in the deck and the soak: licence, serialised, colour, centre.
- No intensifiers: robust, seamless, rigorous, powerful, comprehensive, leverage, crucial, highly, optimal, critical.
- No "we introduce", "we propose a framework", "our evaluation demonstrates", "(i) (ii) (iii)".
- No semicolons. No em dashes. A colon introduces a definition or a list.
- No software version numbers unless a result depends on them.
- No Ansible role names, playbook names or file names in the report body. Name the component by its job: the collector, the stamper, the poller, the projector, the receiver.
- "The collector" is Fluent Bit plus its configuration. "The stamper" is the template service beside it. "The projector" turns alerts into episodes. "The poller" samples the cluster. "The storage tier" is the three replicated machines. "A worker" is an EPN machine.
- Say "not measured" or "not tested", never "beyond the scope of".
- Captions are full sentences that state the point of the figure, as in Roko: "Only the last row is what a user sources."

## The reference voice from the Roko report (to follow)

"The work itself is two commands. Running them takes three systems."
"GitLab can reproduce the lock. It has no equivalent for the pool. That is the only place where the answer is not a plain yes."
"The pipeline's own report was not used as evidence."
"Pointing at production is a decision rather than a code change, and it is not a decision for this project."
"What exists now is groundwork rather than a demonstration."

## The voice to avoid (from the Sotiris report)

"Our evaluation demonstrates that this framework provides a robust, centralized foundation."
"This approach exhibits three key shortcomings: (i) it lacks a single source of truth, (ii) it entangles validation with functional execution, and (iii) it incurs substantial boilerplate."
"seamlessly", "hermetically sealed", "rigorous", "optimal", "critical".

## Structure conventions from all three references

- Front matter: title, author, affiliation, supervisors, group, date, abstract.
- The abstract states the goal, the method, the headline numbers with their reference, and the main limit.
- Order: problem and constraints, what was built, evaluation with measured numbers, limits, what remains, acknowledgements.
- Every headline claim carries a number, and every number is read against a reference: a baseline, a floor, a capacity, the old system.
- Limits and unmeasured things are stated openly.
- End with an explicit "what remains" list.
