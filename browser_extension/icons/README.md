# Extension Icons

You need to create PNG icons in these sizes:
- icon16.png (16x16)
- icon48.png (48x48)
- icon128.png (128x128)

You can use any image editor or an online tool like:
- https://favicon.io/
- https://realfavicongenerator.net/

Suggested design: A simple book emoji (📚) or flashcard icon on a purple (#6366f1) background.

For now, you can use placeholder images by running:
```bash
# Create simple colored squares as placeholders
convert -size 16x16 xc:'#6366f1' icon16.png
convert -size 48x48 xc:'#6366f1' icon48.png
convert -size 128x128 xc:'#6366f1' icon128.png
```

Or download book icons from https://icons8.com/icons/set/book
