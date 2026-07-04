/* Auto-generated telemetry config header. */
#ifndef INFO_CONFIG_H
#define INFO_CONFIG_H

#include <stdint.h>
#include <stdbool.h>
#include "telemtrycustom.h"

/* Generated telemetry IDs for sensors and commands. */
enum
{
    TELEMTRY_ID_TEST = 0x01,
    TELEMTRY_ID_TEST2 = 0x02,
    TELEMTRY_ID_BMP = 0x03,
    TELEMTRY_ID_TEMP = 0x04,
    /* Add only above this line */
    TOTAL_TELEMTRY_ID,
};

typedef struct {
    float temp;
    float pressure;
    float humid;
    char name[7];
} bmp_t;

typedef struct {
    uint8_t field1;
    float field2;
    uint16_t filed3;
    char field4[7];
    uint8_t field5[4];
} temp_t;


extern tel_cmd_t *sensor_array[TOTAL_TELEMTRY_ID-1];
extern tel_information_t *buffers_array[TOTAL_TELEMTRY_ID-1];

#endif /* INFO_CONFIG_H */
