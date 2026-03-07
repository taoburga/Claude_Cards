# Flashcard Quality Guidelines

Guidelines for generating effective Anki flashcards. These are derived from [Piotr Wozniak's 20 Rules of Formulating Knowledge](https://supermemo.guru/wiki/20_rules_of_knowledge_formulation), the [Minimum Information Principle](https://www.supermemo.com/en/blog/twenty-rules-of-formulating-knowledge), and [Precise Anki Cards](https://controlaltbackspace.org/precise/).

These principles are baked into the generation prompt in `flashcard_watcher.py`. To modify how cards are generated, edit the `FLASHCARD_TOOL` schema and the prompt in `create_flashcard_from_image()`.

---

## The Golden Rules

1. **One fact per card.** Never test multiple things. "What are the three branches of government?" is bad. Three separate cards is good.

2. **One unambiguous answer.** If multiple correct answers exist, the card is poorly designed. "Give an example of a mammal" is bad. "A whale is an example of a ___" is good.

3. **Understand before memorizing.** If the user wouldn't understand the card without the source material, the card is useless. The card should make sense standalone.

## Card Design

4. **Front: precise question.** Not "Tell me about X" but "What mechanism causes X?" The question should constrain the answer space.

5. **Back: minimal answer.** Just the fact. No filler, no restating the question, no "The answer is...". If the answer needs more than ~15 words, the card should be split.

6. **Context-free.** The card must stand alone. Don't write "In the article, what did they find?" Write "What did [Author] find about [Topic] in [Year]?"

7. **No yes/no questions.** "Is X true?" teaches nothing. Rephrase as cloze or direct question.

8. **Avoid enumerations.** Lists are hard to memorize as a group. Either break into individual cards or create a mnemonic.

## Cloze Cards

9. **Cloze for definitions and relationships.** "The mitochondria is the {{c1::powerhouse}} of the cell" works well.

10. **One cloze per card.** Multiple cloze deletions on one card test multiple things (violates rule 1).

11. **Cloze the important word.** Delete the keyword, not the filler. "{{c1::Mitochondria}} is the powerhouse of the cell" tests the name. "The mitochondria is the {{c1::powerhouse}} of the cell" tests the function. Choose based on what you need to remember.

## Reverse Cards

12. **Only when both directions matter.** Term -> Definition AND Definition -> Term. Good for vocabulary, bad for most concepts.

## Tags

13. **Use broad categories.** 2-3 tags max. "biology", "cell-biology" not "biology-chapter-3-page-42".

14. **Use consistent naming.** Lowercase, hyphens, no spaces. Follow existing tag patterns in the deck.

## Image Cards

15. **has_diagram = true only when the visual is essential.** A photo of a circuit diagram: yes. A screenshot of a text paragraph: no.

16. **The question should reference the image.** "In the diagram below, what does the red arrow indicate?" not just "What is X?"

## What NOT to Card

17. **Skip trivia.** If you wouldn't use this knowledge, don't card it.

18. **Skip anything easily googleable.** Card things that need to be in your head: concepts, relationships, mental models.

19. **Skip things you already know well.** Duplicate detection helps, but also: don't card obvious things.
