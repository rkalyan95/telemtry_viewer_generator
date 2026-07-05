#include "../Inc/info_config.h"
#include "telemtrycustom.h"
#include <stdint.h>

static float name_data = 0;

tel_information_t sensor_name = {
    .data_synch = TELEMTRY_ID_SYNCH,
    .information_type = TELEMTRY_TYPE_FLOAT,
    .information_id = TELEMTRY_ID_NAME,
    .information_len = sizeof(name_data),
    .information_buffer = &name_data
};

tel_cmd_t cmd_name = {
    .cmd_synch = TELEMTRY_ID_CMD,
    .cmd_id = TELEMTRY_ID_NAME,
    .tx_buffer = (uint8_t *)"name",
    .crc = 0xFFFF
};

tel_cmd_t *sensor_array[TOTAL_TELEMTRY_ID-1] = {

    &cmd_name,
};

tel_information_t *buffers_array[TOTAL_TELEMTRY_ID-1] = {

    &sensor_name,
};
