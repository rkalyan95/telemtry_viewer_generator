#include "../Inc/info_config.h"
#include "telemtrycustom.h"
#include <stdint.h>

static float test_data = 0;

static int8_t test2_data = 0;

tel_information_t sensor_test = {
    .data_synch = TELEMTRY_ID_SYNCH,
    .information_type = TELEMTRY_TYPE_FLOAT,
    .information_id = TELEMTRY_ID_TEST,
    .information_len = sizeof(test_data),
    .information_buffer = &test_data
};

tel_information_t sensor_test2 = {
    .data_synch = TELEMTRY_ID_SYNCH,
    .information_type = TELEMTRY_TYPE_INT8,
    .information_id = TELEMTRY_ID_TEST2,
    .information_len = sizeof(test2_data),
    .information_buffer = &test2_data
};

tel_cmd_t cmd_test = {
    .cmd_synch = TELEMTRY_ID_CMD,
    .cmd_id = TELEMTRY_ID_TEST,
    .tx_buffer = (uint8_t *)"test",
    .crc = 0xFFFF
};

tel_cmd_t cmd_test2 = {
    .cmd_synch = TELEMTRY_ID_CMD,
    .cmd_id = TELEMTRY_ID_TEST2,
    .tx_buffer = (uint8_t *)"test2",
    .crc = 0xFFFF
};

tel_cmd_t *sensor_array[TOTAL_TELEMTRY_ID-1] = {

    &cmd_test,
    &cmd_test2,
};

tel_information_t *buffers_array[TOTAL_TELEMTRY_ID-1] = {

    &sensor_test,
    &sensor_test2,
};
