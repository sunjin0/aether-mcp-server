FROM node:20-alpine

# PDFKit, docx and xlsx are the controlled Node equivalents of the Python
# document stack.  No package manager is exposed through the Skill contract.
RUN npm install --global --omit=dev pdfkit@0.15.0 docx@9.0.0 xlsx@0.18.5 handlebars@4.7.8 \
    && adduser -S -u 10001 sandbox
ENV NODE_PATH=/usr/local/lib/node_modules
USER 10001
