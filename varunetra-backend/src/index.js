require('dotenv').config();

const cors = require('cors');
const compression = require('compression');
const express = require('express');
const prisma = require('./db/prismaClient');
const spillRoutes = require('./routes/spillRoutes');
const { startSpillCacheRefresh } = require('./controllers/spillController');
const aisRoutes = require('./routes/ais');

const app = express();
const parsedPort = Number.parseInt(process.env.PORT || '3001', 10);
const PORT = Number.isInteger(parsedPort) && parsedPort > 0 ? parsedPort : 3001;

app.use(cors());
app.use(compression());
app.use(express.json());
app.use('/api/v1/spills', spillRoutes);
app.use('/api/v1/slicks', spillRoutes);
app.use('/api/v1/vessels', aisRoutes.router);

/**
 * Global error handler
 * Catches all thrown errors and sends proper JSON responses with CORS headers.
 * Must be defined AFTER all routes and other middleware.
 */
app.use((error, req, res, next) => {
  console.error('[API] Request failed:', error);

  // Ensure CORS headers are present even on errors (critical for browser to see the response)
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type');

  // Determine HTTP status code
  const statusCode = error.status || error.statusCode || 500;
  const message = error.message || 'Internal server error';

  // Send JSON error response
  res.status(statusCode).json({
    error: message,
    timestamp: new Date().toISOString(),
    ...(process.env.NODE_ENV === 'development' && { stack: error.stack }),
  });
});

app.listen(PORT, () => {
  console.log(`🚀 Server is running on port ${PORT}`);
  startSpillCacheRefresh();
  aisRoutes.startAisStream();

  prisma.$queryRaw`SELECT 1`
    .then(() => {
      console.log('✅ Database connected successfully!');
    })
    .catch(error => {
      console.error('❌ Database connection failed:', error);
    });
});