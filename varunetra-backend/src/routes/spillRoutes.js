const express = require('express');
const { getReportedCulprits, getSlicks } = require('../controllers/spillController');

const router = express.Router();

router.get('/:spill_id/culprits', getReportedCulprits);
router.get('/', getSlicks);

module.exports = router;
