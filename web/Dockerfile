FROM node:22-alpine

ENV NODE_ENV=production \
    API_ORIGIN=http://api:8000 \
    NEXT_PUBLIC_API_URL=http://api:8000

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]

